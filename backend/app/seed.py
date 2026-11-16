"""Demo data: resets the database and builds a complete pilot through the real API.

    python -m app.cli seed

Everything is created through the same endpoints the UI uses (so audit trails,
reviews, versions and mastery records are all genuine). Works in offline AI mode;
with a working Gemini key the AI-generated parts use Gemini instead.
"""
from __future__ import annotations

import json
import shutil
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from .config import get_settings
from .core.crypto import get_keyring
from .core.security import hash_password
from .db import Base, SessionLocal, engine, init_db, utcnow

SAMPLES = Path(__file__).resolve().parent.parent / "sample_data"
DEMO_PASSWORD = "Demo2026pass"
DOMAIN = "university.edu"

USERS = [
    ("admin", "admin@university.edu", "Ada Admin", None),
    ("instructor", "sara.instructor@university.edu", "Dr Sara Malik", None),
    ("instructor", "omar.instructor@university.edu", "Dr Omar Farooq", None),
    ("exam_staff", "exams@university.edu", "Erin Exams", None),
    ("student", "ayesha.student@university.edu", "Ayesha Khan", "BSCS-2026-001"),
    ("student", "bilal.student@university.edu", "Bilal Ahmed", "BSCS-2026-002"),
    ("student", "chen.student@university.edu", "Chen Wei", "BSCS-2026-003"),
    # Extra accounts with no enrolments or activity: fresh users to try the app from scratch.
    ("admin", "noor.admin@university.edu", "Noor Admin", None),
    ("instructor", "hina.instructor@university.edu", "Dr Hina Raza", None),
    ("student", "sara.student@university.edu", "Sara Iqbal", "BSCS-2026-004"),
    ("student", "usman.student@university.edu", "Usman Tariq", "BSCS-2026-005"),
    ("student", "fatima.student@university.edu", "Fatima Noor", "BSCS-2026-006"),
]


def _ok(r, what):
    if r.status_code >= 400:
        raise RuntimeError(f"Seeding failed at '{what}': {r.status_code} {r.text[:300]}")
    return r.json() if r.content else None


def _reset():
    s = get_settings()
    from . import models  # noqa: F401

    Base.metadata.drop_all(bind=engine)
    init_db()
    for d in (s.upload_dir, s.outbox_dir):
        p = s.path(d)
        if p.exists():
            shutil.rmtree(p)


def _create_users():
    from .models.user import User

    db = SessionLocal()
    ids = {}
    for role, email, name, sn in USERS:
        u = User(role=role, email=email, email_hash=get_keyring().blind_index(email), full_name=name, student_number=sn,
                 password_hash=hash_password(DEMO_PASSWORD), email_verified=True,
                 consent_version=get_settings().consent_version if role == "student" else None,
                 consent_at=utcnow() if role == "student" else None)
        db.add(u)
        db.flush()
        ids[email] = u.id
    db.commit()
    db.close()
    return ids


def run():
    s = get_settings()
    print(f"Seeding demo data into {s.db_url} (AI mode: {s.ai_mode}, key set: {bool(s.gemini_api_key)})")
    _reset()
    ids = _create_users()
    from .main import app

    with TestClient(app, base_url="https://localhost") as c:
        def login(email):
            r = _ok(c.post("/api/auth/login", json={"email": email, "password": DEMO_PASSWORD}), f"login {email}")
            return {"Authorization": f"Bearer {r['access_token']}"}

        A = login("admin@university.edu")
        SARA = login("sara.instructor@university.edu")
        OMAR = login("omar.instructor@university.edu")

        # --- Sprint 1: curriculum ------------------------------------------------
        md = (SAMPLES / "BSCS_curriculum.md").read_bytes()
        v = _ok(c.post("/api/curriculum/upload", headers=A, files={"file": ("BSCS_curriculum.md", md)}), "curriculum upload")
        rv = _ok(c.post(f"/api/outcomes/versions/{v['id']}/start-review", headers=A), "start review")
        math = next(x for x in rv["courses"] if x["code"] == "MATH110")
        clo2 = next(o for o in math["clos"] if o["code"] == "CLO2")
        _ok(c.patch(f"/api/outcomes/clos/{clo2['id']}", headers=A,
                    json={"description": "Construct direct proofs and proofs by contradiction for simple statements."}), "fix ambiguous CLO")
        courses = {x["code"]: x for x in _ok(c.post(f"/api/outcomes/versions/{v['id']}/approve", headers=A), "approve")["courses"]}
        # a criteria override example: CS201 requires 65%
        _ok(c.post("/api/criteria", headers=A, json={"scope_type": "course", "ref_id": courses["CS201"]["id"], "threshold": 65,
                                                     "reason": "Board of Studies: core data-structures course"}), "criteria")

        # --- offerings (NB-1) ------------------------------------------------------
        today = date.today()
        term = _ok(c.post("/api/offerings/terms", headers=A, json={"name": f"Fall {today.year}", "start_date": str(today - timedelta(days=30)),
                                                                   "end_date": str(today + timedelta(days=120))}), "term")
        sections = {}
        for code, inst, cap in (("CS101", "sara.instructor@university.edu", 40), ("CS201", "sara.instructor@university.edu", 30),
                                ("MATH110", "omar.instructor@university.edu", 40)):
            sec = _ok(c.post("/api/offerings/sections", headers=A, json={"course_id": courses[code]["id"], "term_id": term["id"], "code": "A", "capacity": cap}), "section")
            _ok(c.post(f"/api/offerings/sections/{sec['id']}/assign", headers=A, json={"instructor_id": ids[inst], "start_date": str(today - timedelta(days=30))}), "assign")
            sections[code] = sec

        # --- mappings + activation (US-1.3): CS101 and CS201 active, MATH110 left incomplete
        for code in ("CS101", "CS201"):
            cid = courses[code]["id"]
            cov = _ok(c.post(f"/api/mappings/course/{cid}/import-document", headers=SARA), "import mappings")
            for m in cov["mappings"]:
                _ok(c.post(f"/api/mappings/{m['id']}/decide", headers=SARA, json={"decision": "approve"}), "approve mapping")
            _ok(c.post(f"/api/courses/{cid}/activate", headers=SARA), f"activate {code}")
        _ok(c.post(f"/api/mappings/course/{courses['MATH110']['id']}/suggest", headers=OMAR), "suggest MATH110 mappings")

        # --- approved sources (NB-2) -----------------------------------------------
        for code, h in (("CS101", SARA), ("CS201", SARA), ("MATH110", OMAR)):
            src = _ok(c.post("/api/sources/upload", headers=h, data={"course_id": courses[code]["id"], "title": f"{code} lecture notes",
                                                                    "kind": "lecture_notes", "license_note": "Department-owned teaching notes"},
                             files={"file": (f"{code}_lecture_notes.md", (SAMPLES / "sources" / f"{code}_lecture_notes.md").read_bytes())}), "source")
            # the only instructor on the course approves (separation of duties allows self-approval then)
            _ok(c.post(f"/api/sources/{src['id']}/review", headers=h, json={"decision": "approve", "note": "Checked for accuracy and licence"}), "approve source")

        # --- CS101 item bank (US-4.2 / US-6.1) ---------------------------------------
        cs101 = _ok(c.get(f"/api/outcomes/courses/{courses['CS101']['id']}", headers=SARA), "course")
        clo_by_code = {o["code"]: o for o in cs101["clos"]}
        bank = json.loads((SAMPLES / "cs101_items.json").read_text(encoding="utf-8"))
        for code, groups in bank.items():
            for purpose, items in groups.items():
                for it in items:
                    r = _ok(c.post("/api/items", headers=SARA, json={**it, "course_id": cs101["id"], "clo_id": clo_by_code[code]["id"], "purpose": purpose}), "item")
                    _ok(c.post(f"/api/items/{r['id']}/review", headers=SARA, json={"decision": "approve", "note": "Checked"}), "approve item")
        # extra formative items from the AI generator (Gemini or offline), approved; one left pending for the review queue
        gen = _ok(c.post("/api/items/generate", headers=SARA, json={"course_id": cs101["id"], "purpose": "formative", "count": 4}), "generate items")
        for i, it in enumerate(gen["created"]):
            if i == 0:
                continue
            _ok(c.post(f"/api/items/{it['id']}/review", headers=SARA, json={"decision": "approve", "note": "Reviewed"}), "approve generated")
        _ok(c.post(f"/api/diagnostic/publish/{cs101['id']}", headers=SARA), "publish diagnostic")

        # --- learning content (US-5.4) -------------------------------------------------
        for u in cs101["units"]:
            d = _ok(c.post("/api/content/draft", headers=SARA, json={"course_id": cs101["id"], "unit_id": u["id"]}), "draft lesson")
            _ok(c.post(f"/api/content/versions/{d['version']['id']}/review", headers=SARA, json={"decision": "approve", "note": "Published"}), "approve lesson")
        u1 = cs101["units"][0]
        alt = _ok(c.post("/api/content/draft", headers=SARA, json={"course_id": cs101["id"], "unit_id": u1["id"], "kind": "alternate",
                                                                  "clo_id": clo_by_code["CLO2"]["id"]}), "draft alternate")
        _ok(c.post(f"/api/content/versions/{alt['version']['id']}/review", headers=SARA, json={"decision": "approve"}), "approve alternate")

        # --- students (US-2.4 onward) ------------------------------------------------------
        AY = login("ayesha.student@university.edu")
        BI = login("bilal.student@university.edu")
        CH = login("chen.student@university.edu")
        for h in (AY, BI, CH):
            _ok(c.post("/api/enrollment", headers=h, json={"section_id": sections["CS101"]["id"]}), "enrol")

        def shown_answer(q, right: bool) -> int:
            """Options are shuffled per student: find the correct option (or a wrong one) by its text."""
            from .models.items import Item

            _db = SessionLocal()
            it = _db.get(Item, q["id"])
            key_text = it.options[it.answer]
            _db.close()
            k = q["options"].index(key_text)
            return k if right else (k + 1) % len(q["options"])

        def diagnostic(h, good):
            st = _ok(c.post(f"/api/diagnostic/{cs101['id']}/start", headers=h), "start diagnostic")
            codes = {o["id"]: o["code"] for o in cs101["clos"]}
            while st["status"] == "in_progress":
                q = st["question"]
                st = _ok(c.post(f"/api/diagnostic/attempts/{st['attempt_id']}/answer", headers=h,
                                json={"item_id": q["id"], "choice": shown_answer(q, codes[q["clo_id"]] in good)}), "answer")

        def quiz(h, code, n_correct):
            # demo students follow their path: the study steps before this quiz count as done
            from .db import SessionLocal as _S
            from .services import paths as _paths

            me = _ok(c.get("/api/auth/me", headers=h), "me")
            _db = _S()
            _paths.walk_to_quiz(_db, me["id"], cs101["id"], clo_by_code[code]["id"])
            _db.commit()
            _db.close()
            q = _ok(c.post("/api/quiz/start", headers=h, json={"course_id": cs101["id"], "clo_id": clo_by_code[code]["id"]}), "quiz start")
            for i, qq in enumerate(q["questions"]):
                _ok(c.post(f"/api/quiz/attempts/{q['id']}/answer", headers=h,
                           json={"item_id": qq["id"], "choice": shown_answer(qq, i < n_correct)}), "quiz answer")
            return _ok(c.post(f"/api/quiz/attempts/{q['id']}/finish", headers=h), "quiz finish")

        # Ayesha: strong learner, attained CLO1 and CLO3, appeals one quiz on CLO2
        diagnostic(AY, {"CLO1", "CLO3", "CLO4"})
        quiz(AY, "CLO1", 4)
        quiz(AY, "CLO3", 5)
        a2 = quiz(AY, "CLO2", 2)
        _ok(c.post("/api/appeals", headers=AY, json={"quiz_attempt_id": a2["id"], "item_id": a2["questions"][1]["id"],
                                                     "reason": "I think option B is also correct for question 2 - the notes say both."}), "appeal")
        # Bilal: struggling with CLO2 -> repeated failures -> escalation
        diagnostic(BI, set())
        for _ in range(3):
            quiz(BI, "CLO2", 1)
        # Chen: enrolled but not started (shows as inactive in the console)

        # CS201 prerequisite demo: Bilal cannot enrol without CS101 completed
        c.post("/api/enrollment", headers=BI, json={"section_id": sections["CS201"]["id"]})

    print("Done. Sign in at https://localhost:5173 with any of these accounts (password: %s):" % DEMO_PASSWORD)
    for role, email, name, _ in USERS:
        print(f"  {role:<11} {email:<36} {name}")
