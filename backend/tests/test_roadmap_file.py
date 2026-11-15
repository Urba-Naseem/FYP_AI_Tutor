"""Semester roadmap files (study plans): reading PDF / XLSX / text tables, name matching, and applying semesters."""
import io

import pytest

from app.services import roadmap as rm

from .helpers import SAMPLE_MD, activate_course, as_role, login, make_user, setup_offering

# A roadmap like the university's: semester columns, wrapped names, credit rows, a legend below the table.
LINES = [
    (270, 555, 460, "BS Computer Science (Fall2023 to Onwards)"),
    (66, 539, 106, "Semester 1"), (154, 539, 194, "Semester 2"), (225, 539, 264, "Semester 3"),
    (69, 524, 121, "16 Credit Hrs."), (153, 524, 206, "17 Credit Hrs."), (231, 524, 284, "18 Credit Hrs."),
    (70, 511, 118, "Programming"), (59, 501, 130, "Fundamentals (3+1)"),
    (151, 511, 207, "Object Oriented"), (145, 501, 214, "Programming (3+1)"),
    (229, 511, 283, "Data Structures"), (248, 501, 266, "(3+1)"),
    (239, 489, 274, "Computer"), (227, 479, 287, "Organization and"), (220, 469, 292, "Assembly Language"), (248, 459, 266, "(2+1)"),
    (55, 474, 134, "Discrete Structures (3)"),
    (156, 479, 202, "Final Year Project\x02"), (160, 469, 200, "II (3)"),
    (156, 420, 202, "Domain Elective 6"), (156, 356, 202, "Domain Elective 7"),
    (225, 420, 290, "Elective Supporting"), (240, 410, 280, "Courses (3)"),
    (53, 197, 111, "Computing Core"), (151, 202, 205, "Mathematics &"), (161, 192, 199, "Supporting"),
    (52, 180, 66, "46"), (66, 179, 104, "Credit Hrs."), (153, 180, 162, "12"), (165, 179, 203, "Credit Hrs."),
    (900, 180, 960, "134"),
]


def test_layout_reader_columns_wrapped_names_credits_and_legend():
    es = rm.lines_to_entries(LINES)
    got = {(e.semester, e.name, e.credits_text) for e in es}
    assert got == {
        (1, "Programming Fundamentals", "3+1"), (1, "Discrete Structures", "3"),
        (2, "Object Oriented Programming", "3+1"), (2, "Final Year Project II", "3"),
        (2, "Domain Elective 6", None), (2, "Domain Elective 7", None),
        (3, "Data Structures", "3+1"), (3, "Computer Organization and Assembly Language", "2+1"),
        (3, "Elective Supporting Courses", "3"),  # a real course with the same words as a legend label
    }
    assert next(e for e in es if e.name == "Programming Fundamentals").credits == 4


def test_generated_pdf_is_read_end_to_end():
    pytest.importorskip("pypdfium2")
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=landscape(letter))
    for x, sem in ((80, 1), (230, 2), (380, 3)):
        c.drawString(x, 540, f"Semester {sem}")
    c.drawString(80, 510, "Programming"); c.drawString(80, 500, "Fundamentals (3+1)")
    c.drawString(230, 510, "Object Oriented"); c.drawString(230, 500, "Programming (3+1)")
    c.drawString(380, 510, "Data Structures (3+1)")
    c.drawString(80, 470, "Functional English (3)")
    c.save()
    r = rm.read("plan.pdf", buf.getvalue())
    assert r.engine == "pdf-layout"
    assert {(e.semester, e.name) for e in r.entries} == {(1, "Programming Fundamentals"), (1, "Functional English"),
                                                         (2, "Object Oriented Programming"), (3, "Data Structures")}


def test_xlsx_and_text_roadmaps():
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["BS Computer Science"])
    ws.append(["Semester 1", "Semester 2"])
    ws.append(["16 Credit Hrs.", "17 Credit Hrs."])
    ws.append(["Programming\nFundamentals (3+1)", "Object Oriented Programming (3+1)"])
    ws.append(["Discrete Structures (3)", None])
    buf = io.BytesIO()
    wb.save(buf)
    r = rm.read("plan.xlsx", buf.getvalue())
    assert {(e.semester, e.name) for e in r.entries} == {(1, "Programming Fundamentals"), (1, "Discrete Structures"),
                                                         (2, "Object Oriented Programming")}
    t = rm.read("plan.md", b"## Semester 1\n- Programming Fundamentals (3+1)\n- Discrete Structures (3)\nSemester 2: OOP (3+1); CS2124 Data Structures\n")
    assert [(e.semester, e.name) for e in t.entries] == [(1, "Programming Fundamentals"), (1, "Discrete Structures"),
                                                         (2, "OOP"), (2, "CS2124 Data Structures")]
    with pytest.raises(rm.RoadmapError):
        rm.read("plan.md", b"Just some text without any semesters.")
    with pytest.raises(rm.RoadmapError):
        rm.read("plan.exe", b"MZ")


def test_name_matching():
    entries = [rm.Entry(3, "Data Structures"), rm.Entry(4, "Analysis of Algorithms"), rm.Entry(7, "Final Year Project-I"),
               rm.Entry(7, "Advance Database Management Systems"), rm.Entry(4, "Database Systems"),
               rm.Entry(1, "Functional English"), rm.Entry(8, "CS4143 Capstone", code="CS4143")]
    courses = [("CS2124", "Data Structures & Algorithms"), ("CS3613", "Design & Analysis of Algorithms"),
               ("CS4133", "Final Year Project I"), ("CS2144", "Database Systems"), ("CS4143", "Final Year Project II")]
    got = {m.entry.name: (m.course_code, m.by) for m in rm.match(entries, courses)}
    assert got["Data Structures"] == ("CS2124", "similar")
    assert got["Analysis of Algorithms"] == ("CS3613", "similar")
    assert got["Final Year Project-I"] == ("CS4133", "exact")
    assert got["Database Systems"] == ("CS2144", "exact")
    assert got["Advance Database Management Systems"] == (None, "none")  # a different course: not matched
    assert got["Functional English"] == (None, "none")  # roadmap-only (no CLOs)
    assert got["CS4143 Capstone"] == ("CS4143", "code")


ROADMAP_MD = (b"# Study plan\n\n## Semester 1\n- Introduction to Programming (3+1)\n- Discrete Mathematics (3)\n"
              b"- Functional English (3)\n\n## Semester 2\n- Data Structures (3+1)\n- Islamic Studies (2)\n")


def test_upload_with_roadmap_review_correct_and_student_view(client):
    _, ah = as_role(client, "admin")
    r = client.post("/api/curriculum/upload", headers=ah, files={"file": ("BSCS_curriculum.md", SAMPLE_MD),
                                                                  "roadmap": ("plan.md", ROADMAP_MD)})
    assert r.status_code == 201, r.text
    road = r.json()["roadmap"]
    assert road["summary"] == {"entries": 5, "semesters": 2, "matched": 3, "roadmap_only": 2, "courses_without_semester": []}
    match = {e["name"]: e["course_code"] for e in road["entries"]}
    assert match["Introduction to Programming"] == "CS101" and match["Discrete Mathematics"] == "MATH110"
    assert match["Data Structures"] == "CS201" and match["Functional English"] is None
    vid = r.json()["id"]
    # the review starts with the semesters from the roadmap
    rv = client.post(f"/api/outcomes/versions/{vid}/start-review", headers=ah).json()
    assert {c["code"]: c["semester"] for c in rv["courses"]} == {"CS101": 1, "MATH110": 1, "CS201": 2}
    # an admin can correct a match; each course only once; only courses of this curriculum
    eng = next(e for e in road["entries"] if e["name"] == "Functional English")
    math = next(e for e in road["entries"] if e["name"] == "Discrete Mathematics")
    bad = client.put(f"/api/curriculum/versions/{vid}/roadmap", headers=ah, json={"entries": [{"id": eng["id"], "course_code": "CS101"}]})
    assert bad.status_code == 422
    assert client.put(f"/api/curriculum/versions/{vid}/roadmap", headers=ah, json={"entries": [{"id": eng["id"], "course_code": "XX999"}]}).status_code == 422
    ok = client.put(f"/api/curriculum/versions/{vid}/roadmap", headers=ah,
                    json={"entries": [{"id": math["id"], "course_code": None}]}).json()
    assert ok["summary"]["courses_without_semester"] == ["MATH110"]
    tree = client.get(f"/api/outcomes/versions/{vid}", headers=ah).json()
    assert {c["code"]: c["semester"] for c in tree["courses"]}["MATH110"] is None
    client.put(f"/api/curriculum/versions/{vid}/roadmap", headers=ah, json={"entries": [{"id": math["id"], "course_code": "MATH110"}]})
    # approve and activate; the student sees tracked and roadmap-only courses
    m110 = next(c for c in tree["courses"] if c["code"] == "MATH110")
    clo2 = next(o for o in m110["clos"] if o["code"] == "CLO2")
    client.patch(f"/api/outcomes/clos/{clo2['id']}", headers=ah, json={"description": "Construct direct proofs and proofs by contradiction."})
    courses = {c["code"]: c for c in client.post(f"/api/outcomes/versions/{vid}/approve", headers=ah).json()["courses"]}
    for c in courses.values():
        inst = make_user("instructor")
        setup_offering(client, ah, c["id"], inst)
        activate_course(client, login(client, inst.email), c["id"])
    sh = login(client, make_user("student").email)
    plan = client.get("/api/enrollment/roadmap", headers=sh).json()[0]
    status = {c["title"]: c["status"] for s in plan["semesters"] for c in s["courses"]}
    assert plan["current_semester"] == 1
    assert status["Functional English"] == "not_tracked" and status["Islamic Studies"] == "not_tracked"
    assert status["Data Structures"] == "locked"


def test_roadmap_can_be_added_to_an_active_curriculum(client):
    from .helpers import active_course_with_student

    c = active_course_with_student(client)
    vid = c["course"]["curriculum_version_id"]
    r = client.post(f"/api/curriculum/versions/{vid}/roadmap", headers=c["ah"], files={"file": ("plan.md", ROADMAP_MD)})
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["matched"] == 3
    tree = client.get(f"/api/outcomes/courses/{c['course']['id']}", headers=c["ah"]).json()
    assert tree["semester"] == 1  # CS101 is in semester 1 of the plan
    bad = client.post(f"/api/curriculum/versions/{vid}/roadmap", headers=c["ah"], files={"file": ("plan.md", b"no semesters here")})
    assert bad.status_code == 422 and "Semester" in bad.text
