"""Live progress of long uploads (e.g. "Found 19 courses - 7 extracted"), polled by the page while the upload runs.

In memory only: progress is a convenience for the person waiting, never a record. Each entry belongs to the user who
started it and is forgotten an hour after its last update (a server restart forgets it too; the page then says so).
"""
from __future__ import annotations

import re
import threading
import time

_lock = threading.Lock()
_runs: dict[str, dict] = {}
KEEP_SECONDS = 3600
RX_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def valid_id(run_id: str | None) -> bool:
    return bool(run_id and RX_ID.match(run_id))


def start(run_id: str | None, user_id: int, message: str) -> None:
    if not valid_id(run_id):
        return
    now = time.monotonic()
    with _lock:
        for k in [k for k, v in _runs.items() if now - v["_at"] > KEEP_SECONDS]:
            del _runs[k]
        _runs[run_id] = {"_user": user_id, "_at": now, "stage": "reading", "message": message,
                         "courses_total": None, "courses_done": 0, "step": None, "steps": None, "done": False,
                         "result": None, "error": None}


def update(run_id: str | None, **fields) -> None:
    if not valid_id(run_id):
        return
    with _lock:
        r = _runs.get(run_id)
        if r is not None:
            r.update(fields)
            r["_at"] = time.monotonic()


def add_courses(run_id: str | None, n: int) -> None:
    """Thread-safe: parallel extraction parts report the courses they returned."""
    if not valid_id(run_id) or n <= 0:
        return
    with _lock:
        r = _runs.get(run_id)
        if r is not None:
            total = r["courses_total"]
            r["courses_done"] = min(r["courses_done"] + n, total) if total else r["courses_done"] + n
            r["_at"] = time.monotonic()


def get(run_id: str, user_id: int) -> dict | None:
    with _lock:
        r = _runs.get(run_id)
        if r is None or r["_user"] != user_id:
            return None
        return {k: v for k, v in r.items() if not k.startswith("_")}
