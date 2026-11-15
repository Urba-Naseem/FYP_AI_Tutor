"""Low-level Google Gemini client.

All application code goes through `app.services.ai_gateway` (US-13.2), which adds
grounding, answer protection and tracing on top of this module.

Speed / reliability features:
* `schema`   - a JSON Schema the reply must follow (structured output): no broken or wrongly shaped JSON.
* `thinking` - thinking budget; 0 turns the model's hidden reasoning off for extraction tasks (much faster).
* Backup models that refuse this API key (403 / 404) are skipped for hours instead of being retried on every call;
  overloaded models are skipped for a short cool-down.
* Every attempt is bounded by the remaining time budget, so one request never waits much longer than
  `gemini_total_timeout_seconds`.
* If a model rejects an optional feature (400 INVALID_ARGUMENT), the call is retried once without it.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
import threading

from ..config import get_settings


class LLMUnavailable(Exception):
    """Raised when the model cannot be used (offline mode, no key, API error)."""


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int


def ai_enabled() -> bool:
    s = get_settings()
    return s.ai_mode != "offline" and bool(s.gemini_api_key.strip())


_clients: dict[tuple[str, int], object] = {}
_clients_lock = threading.Lock()


def _client(key: str, timeout_ms: int = 30000):
    """One Gemini client per (key, timeout), created once under a lock and kept. (An lru_cache let two threads
    build duplicates; the one not kept was garbage-collected, which closes its connection - parallel requests then
    failed with "the client has been closed".) Timeouts are rounded to 5 s, so only a handful ever exist."""
    with _clients_lock:
        c = _clients.get((key, timeout_ms))
        if c is None:
            from google import genai
            from google.genai import types

            c = _clients[(key, timeout_ms)] = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=timeout_ms))
        return c


_cooldown: dict[str, float] = {}  # model -> monotonic time until which it is skipped (overloaded)
_unavailable: dict[str, float] = {}  # model -> monotonic time until which it is skipped (refused this key / missing)
UNAVAILABLE_SECONDS = 6 * 3600
MIN_DEADLINE = 10  # seconds: Gemini rejects a shorter request deadline

TRANSIENT_CODES = {429, 500, 502, 503, 504}


class RateLimiter:
    """Free-tier protection: at most `rpm` requests in any 60-second window, shared by every thread."""

    def __init__(self):
        import threading

        self._lock = threading.Lock()
        self._sent: list[float] = []

    def wait(self, rpm: int, deadline: float) -> bool:
        """Block until a request may be sent. False if that would pass `deadline` (time.monotonic())."""
        if rpm <= 0:
            return True
        while True:
            with self._lock:
                now = time.monotonic()
                self._sent = [t for t in self._sent if now - t < 60]
                if len(self._sent) < rpm:
                    self._sent.append(now)
                    return True
                wait = 60 - (now - self._sent[0]) + 0.05
            if now + wait > deadline:
                return False
            time.sleep(min(wait, 5))


limiter = RateLimiter()


def retry_after(e: Exception) -> float | None:
    """Gemini's own hint on 429 errors, e.g. 'retryDelay': '13s' or 'Please retry in 7.2s'."""
    m = re.search(r"retry(?:Delay)?['\"]?\s*[:=]?\s*['\"]?(\d+(?:\.\d+)?)s", str(e), re.I) or         re.search(r"retry in (\d+(?:\.\d+)?)\s*s", str(e), re.I)
    return float(m.group(1)) if m else None


def _code(e: Exception) -> int | None:
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    return code if isinstance(code, int) else None


def is_transient(e: Exception) -> bool:
    """Overload / rate-limit / server errors are worth retrying; auth or bad-request errors are not."""
    code = _code(e)
    if code is not None:
        return code in TRANSIENT_CODES
    msg = str(e).upper()
    return any(t in msg for t in ("UNAVAILABLE", "OVERLOADED", "RESOURCE_EXHAUSTED", "DEADLINE", "TIMEOUT", "TIMED OUT", "503", "429"))


def is_refused(e: Exception) -> bool:
    """The model is not available to this key at all (free tier, permission, unknown model)."""
    code = _code(e)
    msg = str(e).upper()
    return code in (403, 404) or "PERMISSION_DENIED" in msg or "NOT_FOUND" in msg or "FREE TIER IS NOT AVAILABLE" in msg


def is_bad_feature(e: Exception) -> bool:
    code = _code(e)
    msg = str(e).upper()
    return (code == 400 or "INVALID_ARGUMENT" in msg) and any(k in msg for k in ("SCHEMA", "THINKING", "BUDGET", "MIME"))


def model_order() -> list[str]:
    s = get_settings()
    configured = [s.gemini_model] + [m.strip() for m in s.gemini_fallback_models.split(",")
                                     if m.strip() and m.strip() != s.gemini_model]
    now = time.monotonic()
    usable = [m for m in configured if _unavailable.get(m, 0) <= now or m == s.gemini_model]
    ready = [m for m in usable if _cooldown.get(m, 0) <= now]
    return ready or usable  # if every model is cooling down, try them anyway


def _config(system, temperature, max_output_tokens, json_mode, schema, thinking):
    from google.genai import types

    kw = dict(system_instruction=system, temperature=temperature, max_output_tokens=max_output_tokens)
    if json_mode or schema is not None:
        kw["response_mime_type"] = "application/json"
    if schema is not None:
        kw["response_json_schema"] = schema
    if thinking is not None:
        kw["thinking_config"] = types.ThinkingConfig(thinking_budget=thinking)
    return types.GenerateContentConfig(**kw)


def generate(prompt: str, system: str | None = None, json_mode: bool = False, temperature: float = 0.2,
             max_output_tokens: int = 4096, schema: dict | None = None, thinking: int | None = None,
             budget: float | None = None) -> LLMResult:
    """`budget` = total seconds this call may take including retries (default gemini_total_timeout_seconds;
    background jobs pass more so they wait out rate limits instead of falling back)."""
    s = get_settings()
    if s.ai_mode == "offline":
        raise LLMUnavailable("AI_MODE=offline")
    if not s.gemini_api_key.strip():
        raise LLMUnavailable("GEMINI_API_KEY is not set")
    t0 = time.perf_counter()
    budget = budget or s.gemini_total_timeout_seconds
    models = model_order()
    last_error: Exception | None = None
    resp, used = None, None
    features = {"schema": schema, "thinking": thinking}
    for model in models:
        for attempt in range(s.gemini_retries + 1):
            remaining = budget - (time.perf_counter() - t0)
            if remaining < MIN_DEADLINE + 2:
                break  # overall budget spent: the caller uses its fallback instead of keeping the user waiting
            if not limiter.wait(s.gemini_rpm, time.monotonic() + remaining - MIN_DEADLINE - 2):
                last_error = LLMUnavailable("free-tier request limit reached; try again in a minute")
                break
            # the deadline is set AFTER any wait for the rate limit, and never below Gemini's 10 s minimum (a 5 s
            # deadline was rejected with 400 "Minimum allowed deadline is 10s" before the request was even tried)
            remaining = budget - (time.perf_counter() - t0)
            timeout_ms = max(MIN_DEADLINE, int(min(s.gemini_request_timeout_seconds, remaining)) // 5 * 5) * 1000
            try:
                cfg = _config(system, temperature, max_output_tokens, json_mode, features["schema"], features["thinking"])
                client = _client(s.gemini_api_key, timeout_ms)  # held for the whole request
                resp = client.models.generate_content(model=model, contents=prompt, config=cfg)
                used = model
                _cooldown.pop(model, None)
                break
            except Exception as e:  # network, quota, overload, permission...
                last_error = e
                if is_bad_feature(e) and (features["thinking"] is not None or features["schema"] is not None):
                    # this model rejects an optional feature: drop thinking first, then the schema, and retry
                    if features["thinking"] is not None:
                        features["thinking"] = None
                    else:
                        features["schema"] = None
                    continue
                if not is_transient(e):
                    if model == models[0] and model == s.gemini_model and not is_refused(e):
                        raise LLMUnavailable(f"Gemini error: {str(e)[:300]}") from e
                    if is_refused(e) and model != s.gemini_model:
                        _unavailable[model] = time.monotonic() + UNAVAILABLE_SECONDS  # stop trying it on every call
                    if model == s.gemini_model and is_refused(e) and len(models) == 1:
                        raise LLMUnavailable(f"Gemini error: {str(e)[:300]}") from e
                    break  # try the next model
                if attempt < s.gemini_retries:
                    # exponential back-off, or Gemini's own "retry after N s" hint on rate limits
                    delay = retry_after(e) or s.gemini_retry_base_seconds * (2 ** attempt)
                    time.sleep(min(delay, max(remaining - 2, 0)))
                else:
                    _cooldown[model] = time.monotonic() + s.gemini_cooldown_seconds
        if resp is not None:
            break
    if resp is None:
        raise LLMUnavailable(f"Gemini unavailable after retries ({', '.join(models)}): {str(last_error)[:250]}") from last_error
    text = resp.text or ""
    if not text.strip():
        raise LLMUnavailable("Gemini returned an empty response")
    usage = getattr(resp, "usage_metadata", None)
    return LLMResult(
        text=text,
        model=used,
        input_tokens=getattr(usage, "prompt_token_count", None),
        output_tokens=getattr(usage, "candidates_token_count", None),
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )


def parse_json(text: str):
    """Parse a JSON answer, tolerating a ```json fence AROUND the whole reply.

    The JSON itself may contain Markdown code fences (e.g. a Python snippet in a question stem),
    so the text is parsed as-is first and only an outer fence is ever removed.
    """
    t = text.strip()
    try:
        return json.loads(t)
    except ValueError:
        pass
    m = re.fullmatch(r"```(?:json|JSON)?\s*\n?(.*)\n?```", t, re.S)
    if m:
        return json.loads(m.group(1).strip())
    start, end = t.find("{"), t.rfind("}")  # prose around the object
    if 0 <= start < end:
        return json.loads(t[start:end + 1])
    return json.loads(t)
