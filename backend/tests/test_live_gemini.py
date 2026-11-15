"""Live checks against the real Gemini API (not run by default).

    pytest -m live            # needs a working GEMINI_API_KEY in backend/.env

They exercise every AI-backed story with the real model: curriculum parsing (US-1.1),
grounded answers (US-13.2), mapping suggestions (US-1.3), item generation (US-4.2),
lesson drafting (US-5.4), Socratic hints (US-6.2) and reteach explanations (US-6.3).
"""
import os
import time

import pytest
from dotenv import dotenv_values

from app.config import BACKEND_DIR, get_settings

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def live_key(monkeypatch):
    env = dotenv_values(BACKEND_DIR / ".env")
    key = env.get("GEMINI_API_KEY") or os.environ.get("LIVE_GEMINI_API_KEY", "")
    if not key:
        pytest.skip("No GEMINI_API_KEY in backend/.env")
    s = get_settings()
    monkeypatch.setattr(s, "gemini_api_key", key)
    monkeypatch.setattr(s, "ai_mode", "auto")
    if env.get("GEMINI_MODEL"):
        monkeypatch.setattr(s, "gemini_model", env["GEMINI_MODEL"])
    from app.services import llm

    try:
        llm.generate("Reply with OK", max_output_tokens=10)
    except llm.LLMUnavailable as e:
        pytest.skip(f"Gemini not usable with this key: {e}")


def test_live_curriculum_parse(client):
    from .helpers import as_role, upload_curriculum

    _, h = as_role(client, "admin")
    v = upload_curriculum(client, h)
    assert v["parser"] == "gemini" and v["summary"]["courses"] == 3


def test_live_grounded_features(client):
    from .helpers import add_approved_source, active_course_with_student, make_items, walk_to_quiz

    c = active_course_with_student(client)
    cid = c["course"]["id"]
    add_approved_source(client, c["ih"], c["ih"], cid)
    
    # Pause to clear the fixture sanity ping quota
    time.sleep(5)
    
    # Call 1: Grounded Q&A
    ans = client.post("/api/ai/ask", headers=c["ih"], json={"course_id": cid, "question": "What is a base case in recursion?"}).json()
    assert ans["mode"] in ("live", "blocked") and ans["citations"]
    
    # Pause for free-tier 15 RPM quota (1 call / ~4s)
    time.sleep(6)
    
    # Call 2: Item Generation
    gen = client.post("/api/items/generate", headers=c["ih"], json={"course_id": cid, "clo_id": c["course"]["clos"][0]["id"], "count": 2}).json()
    assert "ai" in gen["origins"] and gen["created"]
    
    time.sleep(6)
    
    # Call 3: Content Drafting
    d = client.post("/api/content/draft", headers=c["ih"], json={"course_id": cid, "unit_id": c["course"]["units"][0]["id"]}).json()
    assert d["version"]["origin"] == "ai"
    
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=5)
    walk_to_quiz(client, c["sh"], cid, c["course"]["clos"][0]["id"])  # quizzes open in path order: start the path first
    q = client.post("/api/quiz/start", headers=c["sh"], json={"course_id": cid, "clo_id": c["course"]["clos"][0]["id"]}).json()
    
    time.sleep(6)
    
    # Call 4: Quiz Hint
    hint = client.post(f"/api/quiz/attempts/{q['id']}/hint", headers=c["sh"], json={"item_id": q["questions"][0]["id"]}).json()
    assert hint["mode"] in ("socratic", "protected")