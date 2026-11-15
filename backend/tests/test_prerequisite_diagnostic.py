"""Diagnostic rule by semester and prerequisite.

Semester 1: every course has a diagnostic. Semester 2+: a course with a prerequisite uses the student's final result in
that prerequisite to build the learning path (no new test); without a prerequisite, or when the prerequisite has no
final result for the student, the diagnostic is taken as before.
"""
from app.db import SessionLocal
from app.models.outcomes import Course
from app.models.user import User
from app.services import mastery

from .helpers import activate_course, as_role, approve_curriculum, login, make_user, setup_offering


def setup(client, cs101_scores=(100, 50), complete=True):
    """CS101 completed (with the given mastery % per CLO, or none) and enrolment in CS201 (prerequisite CS101)."""
    _, ah = as_role(client, "admin")
    courses = approve_curriculum(client, ah)
    cs101, cs201 = courses["CS101"], courses["CS201"]
    inst = make_user("instructor")
    s101 = setup_offering(client, ah, cs101["id"], inst)
    s201 = setup_offering(client, ah, cs201["id"], inst, term_name="Spring 2027")
    ih = login(client, inst.email)  # the assigned instructor maps and activates
    for c in (cs101, cs201):
        activate_course(client, ih, c["id"])
    student = make_user("student")
    sh = login(client, student.email)
    e = client.post("/api/enrollment", headers=sh, json={"section_id": s101["id"]}).json()
    if cs101_scores:
        db = SessionLocal()
        st = db.get(User, student.id)
        for clo, pct in zip(cs101["clos"], cs101_scores):
            items = [{"item_id": 0, "item_version": 1, "correct": i < pct // 25} for i in range(4)]
            mastery.add_evidence(db, st, clo["id"], "quiz", "test:1", items)
        db.commit()
        db.close()
    if complete:
        assert client.post(f"/api/enrollment/{e['id']}/complete", headers=ah).status_code == 200
    r = client.post("/api/enrollment", headers=sh, json={"section_id": s201["id"]})
    if r.status_code != 201:  # prerequisite not completed: admin override (the fallback case)
        r = client.post("/api/enrollment/admin", headers=ah, json={"section_id": s201["id"], "student_id": student.id,
                                                              "override_prerequisites": True, "reason": "Transfer student, prerequisite taken elsewhere"})
    assert r.status_code == 201, r.text
    return {"ah": ah, "sh": sh, "cs101": cs101, "cs201": cs201, "student": student}


def expected_pct(c, scores):
    n = len(c["cs101"]["clos"])
    return round(sum(scores) / n, 1)  # CLOs without a result count as 0%


def test_course_without_prerequisite_takes_the_diagnostic(client):
    c = setup(client)
    st = client.get(f"/api/diagnostic/{c['cs101']['id']}/status", headers=c["sh"]).json()
    assert st["needed"] is True and st["mode"] == "test" and "no prerequisite" in st["reason"]


def test_prerequisite_result_replaces_the_diagnostic(client):
    c = setup(client, cs101_scores=(100, 50))
    cid = c["cs201"]["id"]
    st = client.get(f"/api/diagnostic/{cid}/status", headers=c["sh"]).json()
    pct = expected_pct(c, (100, 50))
    assert st["needed"] is False and st["mode"] == "prerequisite" and st["score_pct"] == pct
    assert "CS101" in st["reason"]
    # the test itself is refused: it is not needed
    assert client.post(f"/api/diagnostic/{cid}/start", headers=c["sh"]).status_code == 409
    # the path is built from the CS101 result, covering every CS201 CLO
    p = client.post(f"/api/paths/me/{cid}/generate", headers=c["sh"])
    assert p.status_code == 200, p.text
    p = p.json()
    assert "prerequisite" in p["summary"] and "CS101" in p["summary"]
    assert {s["clo_id"] for s in p["steps"]} == {o["id"] for o in c["cs201"]["clos"]}
    expected = {"ready": "R-READY", "developing": "R-DEVELOPING", "not_ready": "R-NOTREADY"}[
        "ready" if pct >= 70 else "developing" if pct >= 40 else "not_ready"]
    assert all(s["rule"] == expected for s in p["steps"])
    assert all("CS101" in s["rationale"] for s in p["steps"] if s["step_type"] in ("review", "learn", "remedial")
               and "prerequisite" in s["rationale"])


def test_no_result_for_prerequisite_falls_back_to_the_diagnostic(client):
    c = setup(client, cs101_scores=(), complete=True)  # completed but no mastery evidence at all
    st = client.get(f"/api/diagnostic/{c['cs201']['id']}/status", headers=c["sh"]).json()
    assert st["needed"] is True and st["mode"] == "test" and "No final result" in st["reason"] and "CS101" in st["reason"]


def test_prerequisite_not_completed_falls_back_to_the_diagnostic(client):
    c = setup(client, cs101_scores=(100, 100), complete=False)
    st = client.get(f"/api/diagnostic/{c['cs201']['id']}/status", headers=c["sh"]).json()
    assert st["needed"] is True and st["mode"] == "test"


def test_semester_one_always_takes_the_diagnostic(client):
    c = setup(client, cs101_scores=(100, 100))
    db = SessionLocal()
    db.get(Course, c["cs201"]["id"]).semester = 1
    db.commit()
    db.close()
    st = client.get(f"/api/diagnostic/{c['cs201']['id']}/status", headers=c["sh"]).json()
    assert st["needed"] is True and st["mode"] == "test" and "Semester 1" in st["reason"]
