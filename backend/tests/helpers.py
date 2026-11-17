"""Helpers for creating users and logging in during tests."""
from app.core.crypto import get_keyring
from app.core.security import hash_password
from app.db import SessionLocal
from app.models.user import User

PASSWORD = "Passw0rd!2026"


def make_user(role="student", email=None, name="Test User", verified=True, student_number=None, password=PASSWORD):
    email = email or f"{role}{make_user.n}@university.edu"
    make_user.n += 1
    db = SessionLocal()
    from app.config import get_settings
    from app.db import utcnow

    u = User(role=role, email=email, email_hash=get_keyring().blind_index(email), full_name=name,
             password_hash=hash_password(password), email_verified=verified, student_number=student_number,
             consent_version=get_settings().consent_version if role == "student" else None,
             consent_at=utcnow() if role == "student" else None)
    db.add(u)
    db.commit()
    db.refresh(u)
    db.close()
    return u


make_user.n = 1


def login(client, email, password=PASSWORD):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def as_role(client, role, **kw):
    u = make_user(role, **kw)
    return u, login(client, u.email)


# ---- curriculum fixtures -------------------------------------------------
from pathlib import Path  # noqa: E402

SAMPLE_MD = (Path(__file__).resolve().parent.parent / "sample_data" / "BSCS_curriculum.md").read_bytes()


def upload_curriculum(client, h, data=SAMPLE_MD):
    r = client.post("/api/curriculum/upload", headers=h, files={"file": ("BSCS_curriculum.md", data)})
    assert r.status_code == 201, r.text
    return r.json()


def start_review(client, h):
    v = upload_curriculum(client, h)
    r = client.post(f"/api/outcomes/versions/{v['id']}/start-review", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def approve_curriculum(client, h):
    """Upload the sample, fix the ambiguous CLO, approve. Returns {code: course_tree}."""
    rv = start_review(client, h)
    math = next(c for c in rv["courses"] if c["code"] == "MATH110")
    clo2 = next(o for o in math["clos"] if o["code"] == "CLO2")
    client.patch(f"/api/outcomes/clos/{clo2['id']}", headers=h,
                 json={"description": "Construct direct proofs and proofs by contradiction for simple statements."})
    r = client.post(f"/api/outcomes/versions/{rv['version']['id']}/approve", headers=h)
    assert r.status_code == 200, r.text
    return {c["code"]: c for c in r.json()["courses"]}


def setup_offering(client, h, course_id, instructor=None, capacity=30, term_name="Fall 2026"):
    """Create (or reuse) a term and a section for a course, optionally assigning an instructor."""
    terms = client.get("/api/offerings/terms", headers=h).json()
    term = next((t for t in terms if t["name"] == term_name), None)
    if not term:
        term = client.post("/api/offerings/terms", headers=h, json={"name": term_name, "start_date": "2026-01-01", "end_date": "2027-12-31"}).json()
    n = len(client.get(f"/api/offerings/sections?course_id={course_id}", headers=h).json()) + 1
    s = client.post("/api/offerings/sections", headers=h, json={"course_id": course_id, "term_id": term["id"], "code": f"S{n}", "capacity": capacity})
    assert s.status_code == 201, s.text
    s = s.json()
    if instructor:
        r = client.post(f"/api/offerings/sections/{s['id']}/assign", headers=h, json={"instructor_id": instructor.id, "start_date": "2026-01-01"})
        assert r.status_code == 200, r.text
        s = r.json()
    return s


SOURCES_DIR = Path(__file__).resolve().parent.parent / "sample_data" / "sources"


def add_approved_source(client, uploader_h, approver_h, course_id, code="CS101"):
    data = (SOURCES_DIR / f"{code}_lecture_notes.md").read_bytes()
    r = client.post("/api/sources/upload", headers=uploader_h, data={"course_id": course_id, "title": f"{code} notes", "kind": "lecture_notes"},
                    files={"file": (f"{code}.md", data)})
    assert r.status_code == 201, r.text
    r = client.post(f"/api/sources/{r.json()['id']}/review", headers=approver_h, json={"decision": "approve", "note": "ok"})
    assert r.status_code == 200, r.text
    return r.json()


def instructor_for(client, ah, *course_ids):
    """A new instructor assigned (by the admin) to a section of each course; returns (user, headers)."""
    inst = make_user("instructor")
    for cid in course_ids:
        setup_offering(client, ah, cid, inst)
    return inst, login(client, inst.email)


def activate_course(client, h, course_id):
    """Import document mappings, approve them, map any remaining CLO manually, activate."""
    cov = client.post(f"/api/mappings/course/{course_id}/import-document", headers=h).json()
    for m in cov["mappings"]:
        if m["status"] == "suggested":
            cov = client.post(f"/api/mappings/{m['id']}/decide", headers=h, json={"decision": "approve"}).json()
    for code in cov["unmapped_clos"]:
        clo = next(o for o in cov["clos"] if o["code"] == code)
        cov = client.post("/api/mappings", headers=h, json={"clo_id": clo["id"], "plo_id": cov["plos"][0]["id"]}).json()
    r = client.post(f"/api/courses/{course_id}/activate", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def active_course_with_student(client, code="CS101", capacity=30):
    """Admin, instructor (assigned), an approved+active course with a section and one enrolled student.
    The admin approves the curriculum and sets up the section; the assigned instructor maps and activates."""
    _, ah = as_role(client, "admin")
    courses = approve_curriculum(client, ah)
    course = courses[code]
    inst = make_user("instructor")
    section = setup_offering(client, ah, course["id"], inst, capacity=capacity)
    ih = login(client, inst.email)
    activate_course(client, ih, course["id"])
    student = make_user("student")
    sh = login(client, student.email)
    r = client.post("/api/enrollment", headers=sh, json={"section_id": section["id"]})
    assert r.status_code == 201, r.text
    return {"ah": ah, "ih": ih, "sh": sh, "course": course, "courses": courses, "section": section,
            "inst": inst, "student": student}


def make_items(client, h, course, purpose="diagnostic", per_clo=3, approve=True):
    """Create (and approve) simple items for every CLO; option 0 is always the correct answer."""
    out = []
    for clo in course["clos"]:
        for i in range(per_clo):
            r = client.post("/api/items", headers=h, json={
                "course_id": course["id"], "clo_id": clo["id"], "purpose": purpose,
                "stem": f"{clo['code']} {purpose} question {i + 1}?", "options": [f"right {i}", f"wrong a{i}", f"wrong b{i}", f"wrong c{i}"],
                "answer_index": 0, "explanation": "Because.", "hints": ["Think about the definition.", "Look at the example."],
                "difficulty": (i % 3) + 1})
            assert r.status_code == 201, r.text
            it = r.json()
            if approve:
                it = client.post(f"/api/items/{it['id']}/review", headers=h, json={"decision": "approve"}).json()
            out.append(it)
    return out


def approved_lessons(client, h, course):
    """Create and approve one manual lesson per unit; returns {unit_id: doc}."""
    out = {}
    from app.db import SessionLocal
    from app.models.content import ContentDoc

    for u in course["units"]:
        db = SessionLocal()
        d = ContentDoc(course_id=course["id"], unit_id=u["id"], kind="lesson", title=f"Unit {u['number']}: {u['title']}")
        db.add(d)
        db.commit()
        did = d.id
        db.close()
        v = client.post(f"/api/content/{did}/versions", headers=h, json={
            "body_md": f"# Unit {u['number']}\n\nInline maths $a^2+b^2=c^2$.\n\n```python\nprint('hi')\n```\n", "change_summary": "initial"}).json()
        out[u["id"]] = client.post(f"/api/content/versions/{v['id']}/review", headers=h, json={"decision": "approve"}).json()
    return out


def published_diagnostic(client, per_clo=3, code="CS101"):
    c = active_course_with_student(client, code=code)
    c["items"] = make_items(client, c["ih"], c["course"], per_clo=per_clo)
    r = client.post(f"/api/diagnostic/publish/{c['course']['id']}", headers=c["ih"])
    assert r.status_code == 200, r.text
    return c


def right(question) -> int:
    """Index (in the student's shuffled order) of the correct option of a test item ("right ..." in make_items)."""
    return next(i for i, o in enumerate(question["options"]) if str(o).startswith("right"))


def wrong(question) -> int:
    return next(i for i, o in enumerate(question["options"]) if not str(o).startswith("right"))


def take_diagnostic(client, c, correct_clos=()):
    """Answer the whole diagnostic: correct for CLO codes in correct_clos, wrong otherwise."""
    st = client.post(f"/api/diagnostic/{c['course']['id']}/start", headers=c["sh"]).json()
    codes = {o["id"]: o["code"] for o in c["course"]["clos"]}
    while st["status"] == "in_progress":
        q = st["question"]
        choice = right(q) if codes[q["clo_id"]] in correct_clos else wrong(q)
        st = client.post(f"/api/diagnostic/attempts/{st['attempt_id']}/answer", headers=c["sh"], json={"item_id": q["id"], "choice": choice}).json()
    return st


def walk_to_quiz(client, sh, course_id, clo_id):
    """The path is followed step by step: complete the student's earlier steps so this CLO's quiz is open."""
    from app.db import SessionLocal
    from app.services import paths

    me = client.get("/api/auth/me", headers=sh).json()
    if client.get(f"/api/paths/me/{course_id}", headers=sh).json() is None:  # no diagnostic: start the path without it
        assert client.post(f"/api/paths/me/{course_id}/generate", headers=sh).status_code == 200
    db = SessionLocal()
    paths.walk_to_quiz(db, me["id"], course_id, clo_id)
    db.commit()
    db.close()


def take_quiz(client, sh, course_id, clo_id, n_correct, step_id=None, out_of=5):
    """Start a formative quiz and answer n_correct of every `out_of` questions correctly; returns the finish result.
    Quizzes used to have 5 questions and many tests say "4 right" meaning 80%: the count is scaled to the real quiz
    length (formative quizzes now have 10). Pass out_of=10 for a 10-question quiz to give an exact count."""
    walk_to_quiz(client, sh, course_id, clo_id)
    q = client.post("/api/quiz/start", headers=sh, json={"course_id": course_id, "clo_id": clo_id, "step_id": step_id})
    assert q.status_code == 200, q.text
    q = q.json()
    n_right = round(n_correct * len(q["questions"]) / out_of)
    for i, question in enumerate(q["questions"]):
        client.post(f"/api/quiz/attempts/{q['id']}/answer", headers=sh, json={"item_id": question["id"], "choice": right(question) if i < n_right else wrong(question)})
    r = client.post(f"/api/quiz/attempts/{q['id']}/finish", headers=sh)
    assert r.status_code == 200, r.text
    return r.json()
