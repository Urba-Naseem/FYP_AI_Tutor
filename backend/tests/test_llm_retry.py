"""Gemini client resilience: transient errors (503/429) are retried, then a fallback model is tried."""
import pytest

from app.config import get_settings
from app.services import llm


class FakeErr(Exception):
    def __init__(self, code):
        super().__init__(f"{code} error")
        self.code = code


class FakeResp:
    text = "OK"
    usage_metadata = None


def setup(monkeypatch, behaviour):
    s = get_settings()
    monkeypatch.setattr(s, "ai_mode", "auto")
    monkeypatch.setattr(s, "gemini_api_key", "test-key")
    monkeypatch.setattr(s, "gemini_model", "main-model")
    monkeypatch.setattr(s, "gemini_fallback_models", "backup-model")
    monkeypatch.setattr(s, "gemini_retries", 2)
    monkeypatch.setattr(s, "gemini_retry_base_seconds", 0)
    monkeypatch.setattr(s, "gemini_rpm", 0)  # the rate limiter has its own tests below
    calls = []

    class Models:
        def generate_content(self, model, contents, config):
            calls.append(model)
            return behaviour(model, len(calls))

    class Client:
        models = Models()

    monkeypatch.setattr(llm, "_client", lambda *a: Client())
    llm._cooldown.clear()
    llm._unavailable.clear()
    return calls


def test_retries_503_then_succeeds(monkeypatch):
    def b(model, n):
        if n < 3:
            raise FakeErr(503)
        return FakeResp()
    calls = setup(monkeypatch, b)
    assert llm.generate("hi").model == "main-model" and calls == ["main-model"] * 3


def test_falls_back_to_backup_model_when_main_overloaded(monkeypatch):
    def b(model, n):
        if model == "main-model":
            raise FakeErr(503)
        return FakeResp()
    calls = setup(monkeypatch, b)
    assert llm.generate("hi").model == "backup-model" and calls.count("main-model") == 3


def test_permanent_errors_are_not_retried(monkeypatch):
    """A model the key is refused on (403) is not retried; the next model is tried once instead."""
    def b(model, n):
        raise FakeErr(403)
    calls = setup(monkeypatch, b)
    with pytest.raises(llm.LLMUnavailable):
        llm.generate("hi")
    assert calls == ["main-model", "backup-model"]


def test_refused_backup_is_remembered(monkeypatch):
    """A backup model the free tier refuses is skipped on later calls (it failed 13 times in the AI log)."""
    def b(model, n):
        if model == "main-model":
            raise FakeErr(503)
        raise FakeErr(403)
    calls = setup(monkeypatch, b)
    with pytest.raises(llm.LLMUnavailable):
        llm.generate("first")
    calls.clear()
    llm._cooldown.clear()
    with pytest.raises(llm.LLMUnavailable):
        llm.generate("second")
    assert "backup-model" not in calls


def test_rate_limiter_caps_requests_per_minute(monkeypatch):
    import time

    lim = llm.RateLimiter()
    soon = time.monotonic() + 0.5
    assert all(lim.wait(3, soon) for _ in range(3))
    assert lim.wait(3, soon) is False  # a 4th request in the same minute would pass the deadline
    assert lim.wait(0, soon) is True  # 0 = no limit


def test_retry_after_reads_gemini_hint():
    assert llm.retry_after(Exception("429 RESOURCE_EXHAUSTED {'retryDelay': '13s'}")) == 13
    assert llm.retry_after(Exception("Please retry in 7.2s.")) == pytest.approx(7.2)
    assert llm.retry_after(Exception("503 overloaded")) is None


def test_gives_up_after_all_models(monkeypatch):
    calls = setup(monkeypatch, lambda m, n: (_ for _ in ()).throw(FakeErr(503)))
    with pytest.raises(llm.LLMUnavailable, match="after retries"):
        llm.generate("hi")
    assert len(calls) == 6


def test_missing_backup_model_is_skipped(monkeypatch):
    s = get_settings()

    def b(model, n):
        if model == "main-model":
            raise FakeErr(503)
        if model == "gone-model":
            raise FakeErr(404)
        return FakeResp()
    calls = setup(monkeypatch, b)
    monkeypatch.setattr(s, "gemini_fallback_models", "gone-model,backup-model")
    assert llm.generate("hi").model == "backup-model" and calls.count("gone-model") == 1


def test_parse_json_keeps_code_fences_inside_strings():
    raw = r'{"items":[{"stem":"What prints?\n\n```python\nprint(1)\n```","options":["1","2","3","4"]}]}'
    assert "```python" in llm.parse_json(raw)["items"][0]["stem"]
    assert llm.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm.parse_json('Here you go:\n{"a": 2}') == {"a": 2}


def test_overloaded_model_is_skipped_on_next_call(monkeypatch):
    def b(model, n):
        if model == "main-model":
            raise FakeErr(503)
        return FakeResp()
    calls = setup(monkeypatch, b)
    llm.generate("first")
    calls.clear()
    assert llm.generate("second").model == "backup-model"
    assert calls == ["backup-model"]  # main model is cooling down, no wasted waiting
