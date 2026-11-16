"""Each student gets their own questions: only the diagnostic structure is shared.

- formative / reteach quizzes: from the teacher-approved bank, never repeating a question for the same student while
  unseen ones remain, difficulty matched to the student's level, a per-student choice among equally good questions;
- the diagnostic: same difficulty rules, a different question for each student;
- past quizzes: reviewable (answers, correct answers, explanations) by their owner only;
- units: proposed for documents that list none (AI grouping, even-split fallback);
- reteach: the explanation is written around the student's own wrong answers.
"""
import json

from app.db import SessionLocal
from app.models.items import Item
from app.services import llm, unit_planner
from app.services.reteach import recent_mistakes

from .helpers import walk_to_quiz
from .helpers import right as opt_right, wrong as opt_wrong
from .helpers import active_course_with_student, login, make_items, make_user, published_diagnostic


def second_student(client, c):
    s = make_user("student")
    sh = login(client, s.email)
    r = client.post("/api/enrollment", headers=sh, json={"section_id": c["section"]["id"]})
    assert r.status_code == 201, r.text
    return s, sh


def start(client, sh, c, clo):
    walk_to_quiz(client, sh, c["course"]["id"], clo["id"])  # quizzes open in path order: start the path first
    r = client.post("/api/quiz/start", headers=sh, json={"course_id": c["course"]["id"], "clo_id": clo["id"]})
    assert r.status_code == 200, r.text
    return r.json()


def finish(client, sh, q, right=0):
    for i, question in enumerate(q["questions"]):
        client.post(f"/api/quiz/attempts/{q['id']}/answer", headers=sh, json={"item_id": question["id"], "choice": opt_right(question) if i < right else opt_wrong(question)})
    r = client.post(f"/api/quiz/attempts/{q['id']}/finish", headers=sh)
    assert r.status_code == 200, r.text
    return r.json()


def test_two_students_get_different_question_sets(client):
    c = active_course_with_student(client)
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=15)
    clo = c["course"]["clos"][0]
    _, sh2 = second_student(client, c)
    a = {x["id"] for x in start(client, c["sh"], c, clo)["questions"]}
    b = {x["id"] for x in start(client, sh2, c, clo)["questions"]}
    assert len(a) == len(b) == 5 and a != b


def test_a_student_never_repeats_a_question_while_unseen_ones_remain(client):
    c = active_course_with_student(client)
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=12)
    clo = c["course"]["clos"][0]
    seen = set()
    for _ in range(2):
        q = start(client, c["sh"], c, clo)
        ids = {x["id"] for x in q["questions"]}
        assert not ids & seen  # 10 of 12 used, no repeats
        seen |= ids
        finish(client, c["sh"], q)


def test_difficulty_follows_the_students_level(client):
    c = active_course_with_student(client)
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=15)  # 5 easy, 5 medium, 5 hard
    clo = c["course"]["clos"][0]
    q = finish(client, c["sh"], start(client, c["sh"], c, clo), right=0)  # 0% -> mastery 0
    assert q["mastery"]["mastery_pct"] == 0
    nxt = start(client, c["sh"], c, clo)
    db = SessionLocal()
    diffs = [db.get(Item, x["id"]).difficulty for x in nxt["questions"]]
    db.close()
    assert diffs.count(1) >= 3 and 3 not in diffs, diffs  # a struggling student: mostly easy, never hard


def test_diagnostic_same_difficulty_different_questions(client):
    c = published_diagnostic(client, per_clo=9)  # 3 per difficulty level for every CLO
    _, sh2 = second_student(client, c)
    cid = c["course"]["id"]
    first = [client.post(f"/api/diagnostic/{cid}/start", headers=h).json()["question"] for h in (c["sh"], sh2)]
    db = SessionLocal()
    items = [db.get(Item, q["id"]) for q in first]
    db.close()
    assert items[0].difficulty == items[1].difficulty == 2  # same rule: start at medium
    # over many students the first question varies (stable per student, different between students)
    ids = {items[0].id, items[1].id}
    for _ in range(6):
        _, h = second_student(client, c)
        ids.add(client.post(f"/api/diagnostic/{cid}/start", headers=h).json()["question"]["id"])
    assert len(ids) > 1


def test_past_quizzes_show_answers_to_their_owner_only(client):
    c = active_course_with_student(client)
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=6)
    clo = c["course"]["clos"][0]
    q = finish(client, c["sh"], start(client, c["sh"], c, clo), right=3)
    hist = client.get(f"/api/quiz/history/{c['course']['id']}", headers=c["sh"]).json()
    assert len(hist) == 1 and hist[0]["correct"] == 3 and hist[0]["total"] == 5
    review = client.get(f"/api/quiz/attempts/{q['id']}", headers=c["sh"]).json()
    assert all("answer" in x and "correct_index" in x["answer"] and "explanation" in x["answer"] for x in review["questions"])
    _, sh2 = second_student(client, c)
    assert client.get(f"/api/quiz/attempts/{q['id']}", headers=sh2).status_code == 404
    assert client.get(f"/api/quiz/history/{c['course']['id']}", headers=sh2).json() == []


def test_reteach_explanation_uses_the_students_mistakes(client, monkeypatch):
    c = active_course_with_student(client)
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=6)
    clo = c["course"]["clos"][0]
    finish(client, c["sh"], start(client, c["sh"], c, clo), right=1)
    db = SessionLocal()
    m = recent_mistakes(db, c["student"].id, clo["id"])
    db.close()
    assert m and all("Their answer:" in x and "Correct answer:" in x for x in m)
    prompts = []
    monkeypatch.setattr(llm, "generate", lambda prompt, *a, **k: (prompts.append(prompt), llm.LLMResult("A fresh explanation.", "gemini-test", 1, 1, 1))[1])
    r = client.get(f"/api/reteach/{c['course']['id']}/clo/{clo['id']}", headers=c["sh"])
    assert r.status_code == 200, r.text
    assert any("LAST ANSWERS THAT WERE WRONG" in p for p in prompts)


CLOS = [{"code": f"CLO{i}", "description": d, "unit": None} for i, d in enumerate(
    ["Explain how variables and data types work", "Write programs using if statements", "Write loops to repeat work",
     "Define and call functions", "Use lists and strings"], start=1)]


def test_even_split_fallback():
    units = unit_planner.even_split(CLOS)
    assert [u["clos"] for u in units] == [["CLO1", "CLO2"], ["CLO3", "CLO4"], ["CLO5"]]
    assert all(u["title"] for u in units)


def test_ai_unit_plan_is_used_when_valid_and_replaced_when_not(monkeypatch):
    good = {"courses": [{"code": "CS101", "units": [{"title": "Basics", "clos": ["CLO1", "CLO2"]}, {"title": "Repetition and reuse", "clos": ["CLO3", "CLO4", "CLO5"]}]},
                        {"code": "CS102", "units": [{"title": "Everything", "clos": ["CLO1"]}]}]}  # CS102 plan drops CLOs -> fallback
    monkeypatch.setattr(llm, "generate", lambda *a, **k: llm.LLMResult(json.dumps(good), "gemini-test", 1, 1, 1))
    parsed = {"courses": [{"code": "CS101", "title": "Programming", "auto_unit": True, "units": [{"number": 1, "title": "Course content", "clos": []}], "clos": [dict(x) for x in CLOS]},
                          {"code": "CS102", "title": "Programming 2", "auto_unit": True, "units": [], "clos": [dict(x) for x in CLOS]}]}
    db = SessionLocal()
    assert unit_planner.propose(db, None, parsed) == 2
    db.close()
    a, b = parsed["courses"]
    assert [u["title"] for u in a["units"]] == ["Basics", "Repetition and reuse"] and a["units_proposed"] and not a["auto_unit"]
    assert {o["code"]: o["unit"] for o in a["clos"]} == {"CLO1": 1, "CLO2": 1, "CLO3": 2, "CLO4": 2, "CLO5": 2}
    assert len(b["units"]) == 3  # invalid AI plan -> even split


def test_upload_without_units_gets_proposed_units(client):
    from .helpers import as_role

    _, ah = as_role(client, "admin")
    doc = ("# Program: BSCS - Computer Science\n\n## Programme Learning Outcomes\n- PLO1: Apply computing knowledge.\n\n"
           "## Course: CS101 - Programming\nCredits: 3\nPrerequisites: None\n\n### Course Learning Outcomes\n"
           + "\n".join(f"- {o['code']}: {o['description']}." for o in CLOS) + "\n")
    v = client.post("/api/curriculum/upload", headers=ah, files={"file": ("bscs.md", doc.encode())}).json()
    course = v["parsed"]["courses"][0]
    assert len(course["units"]) == 3 and course["units_proposed"]  # no AI in tests -> even split
    assert any("units were proposed" in w["message"] for w in v["warnings"])
