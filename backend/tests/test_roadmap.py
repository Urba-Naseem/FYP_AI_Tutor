"""Semester roadmap: extracted from the curriculum document and used to limit enrolment."""
from app.services import curriculum_parser as cp

from .helpers import SAMPLE_MD, activate_course, as_role, login, make_user, setup_offering, upload_curriculum

ROADMAP = b"\n## Roadmap\n\n- Semester 1: CS101\n- Semester 2: MATH110\n- Semester 3: CS201\n"


def test_parser_reads_roadmap_lists_fields_and_headings():
    md = """# Program: BSX - Test
- PLO1: Apply computing knowledge to solve problems well.
- Semester 1: CS101
## Course: CS101 - Intro
- Unit 1: Basics — CLO1
- CLO1: Explain variables and data types in Python programs. (Unit 1)
## Year 2 - Fall
## Course: CS301 - Systems
Credits: 3 Semester: 4
- Unit 1: X — CLO1
- CLO1: Explain operating system scheduling clearly. (Unit 1)
## Year 2 - Spring
## Course: CS302 - Networks
- Unit 1: X — CLO1
- CLO1: Explain how packets are routed in networks. (Unit 1)
## Course: CS999 - Elective
- Unit 1: X — CLO1
- CLO1: Explain a chosen advanced computing topic. (Unit 1)
"""
    p = cp.normalise(cp.heuristic_parse(md))
    sem = {c["code"]: c["semester"] for c in p["courses"]}
    assert sem["CS101"] == 1 and sem["CS301"] == 4 and sem["CS302"] == 4
    # a course under "Year 2 - Spring" heading is semester 4; the elective after it inherits it too
    assert sem["CS999"] == 4
    assert cp.normalise({"courses": [{"code": "X1", "semester": "abc"}]})["courses"][0]["semester"] is None


def test_missing_roadmap_is_only_information():
    p = cp.normalise(cp.heuristic_parse(SAMPLE_MD.decode()))
    issues = cp.check_structure(p)
    assert any(i["level"] == "info" and i["path"] == "roadmap" for i in issues)


def setup(client):
    _, ah = as_role(client, "admin")
    v = upload_curriculum(client, ah, SAMPLE_MD + ROADMAP)
    rv = client.post(f"/api/outcomes/versions/{v['id']}/start-review", headers=ah).json()
    assert {c["code"]: c["semester"] for c in rv["courses"]} == {"CS101": 1, "MATH110": 2, "CS201": 3}
    math = next(c for c in rv["courses"] if c["code"] == "MATH110")
    clo2 = next(o for o in math["clos"] if o["code"] == "CLO2")
    client.patch(f"/api/outcomes/clos/{clo2['id']}", headers=ah, json={"description": "Construct direct proofs and proofs by contradiction."})
    courses = {c["code"]: c for c in client.post(f"/api/outcomes/versions/{v['id']}/approve", headers=ah).json()["courses"]}
    sections = {}
    for code, c in courses.items():
        inst = make_user("instructor")
        sections[code] = setup_offering(client, ah, c["id"], inst)
        activate_course(client, login(client, inst.email), c["id"])
    student = make_user("student")
    return ah, student, login(client, student.email), sections


def test_enrolment_is_limited_to_the_current_semester(client):
    ah, student, sh, sections = setup(client)
    cat = {c["code"]: c for c in client.get("/api/enrollment/catalog", headers=sh).json()}
    assert cat["CS101"]["semester"] == 1 and cat["CS101"]["roadmap_problem"] is None
    assert "Semester 2" in cat["MATH110"]["roadmap_problem"] and "complete CS101 first" in cat["MATH110"]["roadmap_problem"]
    r = client.post("/api/enrollment", headers=sh, json={"section_id": sections["MATH110"]["id"]})
    assert r.status_code == 409 and "Semester 2" in r.text
    e = client.post("/api/enrollment", headers=sh, json={"section_id": sections["CS101"]["id"]})
    assert e.status_code == 201
    rm = client.get("/api/enrollment/roadmap", headers=sh).json()[0]
    status = {c["code"]: c["status"] for s in rm["semesters"] for c in s["courses"]}
    assert rm["current_semester"] == 1 and status == {"CS101": "enrolled", "MATH110": "locked", "CS201": "locked"}
    # completing semester 1 moves the student on to semester 2
    assert client.post(f"/api/enrollment/{e.json()['id']}/complete", headers=ah).status_code == 200
    rm = client.get("/api/enrollment/roadmap", headers=sh).json()[0]
    assert rm["current_semester"] == 2
    assert client.post("/api/enrollment", headers=sh, json={"section_id": sections["MATH110"]["id"]}).status_code == 201
    # semester 3 is still locked for the student, but an administrator can override with a reason
    assert client.post("/api/enrollment", headers=sh, json={"section_id": sections["CS201"]["id"]}).status_code == 409
    r = client.post("/api/enrollment/admin", headers=ah, json={"student_id": student.id, "section_id": sections["CS201"]["id"],
                                                               "override_prerequisites": True, "reason": "Transfer student"})
    assert r.status_code == 201, r.text


def test_semester_editable_in_review_and_existing_db_gets_column(client, db):
    from sqlalchemy import inspect

    from app import db as dbmod
    _, ah = as_role(client, "admin")
    v = upload_curriculum(client, ah)
    rv = client.post(f"/api/outcomes/versions/{v['id']}/start-review", headers=ah).json()
    c = rv["courses"][0]
    assert c["semester"] is None
    r = client.patch(f"/api/outcomes/courses/{c['id']}", headers=ah, json={"semester": 2})
    assert r.status_code == 200 and r.json()["semester"] == 2
    assert client.patch(f"/api/outcomes/courses/{c['id']}", headers=ah, json={"semester": 0}).status_code == 422
    dbmod.add_missing_columns()  # idempotent on an up-to-date database
    assert "semester" in {col["name"] for col in inspect(dbmod.engine).get_columns("courses")}
