"""Roadmap inside the curriculum document; grouped notes; problems a person fixes in review, then automation goes on."""
from app.services import curriculum_parser as cp
from app.services import roadmap

from .helpers import SAMPLE_MD, as_role
from .test_automation import auto, fake_ai  # noqa: F401  (fixture + fake model)

ROADMAP_TABLE = """
## 2. Roadmap

Semester Courses (all covered in this document)
Semester 1 Introduction to Programming � Discrete
Mathematics � Functional English
Semester 2 Data Structures � Domain Elective 1 (Web Application Development)

## 3. Courses
"""


def test_semester_rows_inside_a_document_are_read():
    e = roadmap.embedded_entries(ROADMAP_TABLE)
    assert [(x.semester, x.name) for x in e] == [
        (1, "Introduction to Programming"), (1, "Discrete Mathematics"), (1, "Functional English"),
        (2, "Data Structures"), (2, "Domain Elective 1 (Web Application Development)")]
    assert roadmap.embedded_entries("Semester 1 starts in September.\nSome other text") == []  # not a roadmap


def test_names_in_brackets_and_acronyms_match():
    assert roadmap.name_score("Domain Elective 1 (Web Application Development)", "Web Application Development") == 1.0
    assert roadmap.name_score("Application of ICT", "Application of Information and Communication Technologies") == 1.0


def test_upload_uses_the_roadmap_table_in_the_document(client):
    _, ah = as_role(client, "admin")
    text = SAMPLE_MD.decode()
    doc = text.replace("## Course: CS101", ROADMAP_TABLE + "\n## Course: CS101", 1)
    r = client.post("/api/curriculum/upload", headers=ah, files={"file": ("bscs.md", doc.encode())})
    assert r.status_code == 201, r.text
    v = r.json()
    assert v["roadmap"] and v["roadmap"]["engine"] == "document"
    rv = client.post(f"/api/outcomes/versions/{v['id']}/start-review", headers=ah).json()
    sem = {c["code"]: c["semester"] for c in rv["courses"]}
    assert sem["CS101"] == 1 and sem["MATH110"] == 1 and sem["CS201"] == 2


def parsed_without_units(n=3):
    return cp.normalise({"program": {"code": "BSCS", "name": "BSCS"},
                         "plos": [{"code": "PLO1", "description": "Apply knowledge of computing fundamentals."}],
                         "courses": [{"code": f"CS10{i}", "title": f"Course {i}", "credits": 3, "units": [],
                                      "clos": [{"code": "CLO1", "description": f"Explain topic number {i} clearly."}]}
                                     for i in range(n)]})


def test_a_document_without_units_gives_one_note_not_a_warning_per_course():
    issues = cp.check_structure(parsed_without_units())
    unit_msgs = [i for i in issues if "unit" in i["message"].lower()]
    assert len(unit_msgs) == 1 and unit_msgs[0]["level"] == "info"


def test_courses_outside_the_roadmap_are_one_note():
    p = parsed_without_units()
    p["courses"][0]["semester"] = 1
    issues = [i for i in cp.check_structure(p) if "semester" in i["message"].lower()]
    assert len(issues) == 1 and issues[0]["level"] == "info" and "CS101" in issues[0]["message"]


def test_repeated_code_stops_automation_until_fixed_then_it_continues(client, auto, monkeypatch):  # noqa: F811
    fake_ai(monkeypatch)
    _, ah = as_role(client, "admin")
    text = SAMPLE_MD.decode()
    extra = text[text.index("## Course: CS201"):text.index("## Course: MATH110")].replace(
        "CS201 - Data Structures", "CS201 - Graph Theory")
    v = client.post("/api/curriculum/upload", headers=ah, files={"file": ("dup.md", (text + "\n\n" + extra).encode())}).json()
    a = v["automation"]
    assert a["status"] == "in_review" and a["stopped"] and any("CS201 is used for 2 courses" in e for e in a["errors"])
    # the admin gives the second course its own code and sets a CLO weight, then approves
    rv = client.get(f"/api/outcomes/versions/{v['id']}", headers=ah).json()
    graph = next(c for c in rv["courses"] if c["title"] == "Graph Theory")
    assert client.patch(f"/api/outcomes/courses/{graph['id']}", headers=ah, json={"code": "CS205"}).status_code == 200
    clo = graph["clos"][0]
    assert client.patch(f"/api/outcomes/clos/{clo['id']}", headers=ah, json={"weight": 40}).status_code == 200
    assert client.patch(f"/api/outcomes/clos/{clo['id']}", headers=ah, json={"weight": 140}).status_code == 422
    r = client.post(f"/api/outcomes/versions/{v['id']}/ack-all", headers=ah, json={"note": "Checked against the document"})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/outcomes/versions/{v['id']}/approve", headers=ah)
    assert r.status_code == 200, r.text
    courses = client.get("/api/outcomes/courses", headers=ah).json()
    mine = [c for c in courses if c["curriculum_version_id"] == v["id"]]
    assert mine and all(c["status"] == "active" for c in mine)  # linking and activation continued by themselves
    tree = client.get(f"/api/outcomes/versions/{v['id']}", headers=ah).json()
    w = next(o for c in tree["courses"] if c["code"] == "CS205" for o in c["clos"] if o["id"] == clo["id"])["weight"]
    assert w == 40


OTHER_LAYOUT_PLAN = """
Programme of Studies

Year 1 - Fall term
  - Introduction to Programming
  - Discrete Mathematics
Year 1 - Spring term
  - Data Structures
"""


def test_a_roadmap_in_another_layout_is_read_by_the_ai(client, monkeypatch):
    """No 'Semester N a • b' rows: the passages that name terms/years go to the AI, which returns the study plan."""
    import json

    from app.services import llm

    asked = []

    def gen(prompt, system=None, **kw):
        if "study plans" in (system or ""):
            asked.append(prompt)
            rows = [{"semester": 1, "name": "Introduction to Programming"}, {"semester": 1, "name": "Discrete Mathematics"},
                    {"semester": 2, "name": "Data Structures"}]
            return llm.LLMResult(text=json.dumps({"courses": rows}), model="gemini-test", input_tokens=1, output_tokens=1, latency_ms=1)
        raise llm.LLMUnavailable("only the roadmap reader is faked here")  # curriculum: template parser

    monkeypatch.setattr(llm, "generate", gen)
    assert roadmap.embedded_entries(OTHER_LAYOUT_PLAN) == []  # not the quick layout
    assert "Year 1 - Spring term" in roadmap.roadmap_passages(OTHER_LAYOUT_PLAN)
    _, ah = as_role(client, "admin")
    doc = SAMPLE_MD.decode().replace("## Course: CS101", OTHER_LAYOUT_PLAN + "\n## Course: CS101", 1)
    v = client.post("/api/curriculum/upload", headers=ah, files={"file": ("bscs.md", doc.encode())}).json()
    assert asked and v["roadmap"]["engine"] == "document-ai"
    rv = client.post(f"/api/outcomes/versions/{v['id']}/start-review", headers=ah).json()
    sem = {c["code"]: c["semester"] for c in rv["courses"]}
    assert sem["CS101"] == 1 and sem["MATH110"] == 1 and sem["CS201"] == 2


def test_documents_that_barely_mention_semesters_are_not_sent_to_the_ai():
    assert roadmap.roadmap_passages("This course is offered every semester.\nCLO1: Explain loops.") == ""


def test_prerequisites_that_are_not_course_codes_are_dropped():
    p = cp.normalise({"program": {"code": "BSCS", "name": "BSCS"}, "plos": [],
                      "courses": [{"code": "CS1113", "title": "Software Engineering", "prerequisites": ["S", "CS 1134", "none", "DE-1"],
                                   "clos": [{"code": "CLO1", "description": "Understand software engineering principles."}]}]})
    assert p["courses"][0]["prerequisites"] == ["CS1134", "DE1"]
