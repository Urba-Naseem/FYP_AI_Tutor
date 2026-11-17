"""US-6.1 Deliver formative quizzes and update mastery.

Formative quizzes have at least 10 questions (bank MCQs plus AI-written true/false, fill-in-the-blank and short-answer
questions), save answers without marking them, and reveal the full result only after submission."""
import json

from app.services import llm

from .helpers import walk_to_quiz
from .helpers import right, wrong
from .helpers import active_course_with_student, make_items, take_quiz


def ctx(client, per_clo=10):
    c = active_course_with_student(client)
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=per_clo)
    c["clo"] = c["course"]["clos"][0]
    return c


def start(client, c):
    walk_to_quiz(client, c["sh"], c["course"]["id"], c["clo"]["id"])  # quizzes open in path order: start the path first
    return client.post("/api/quiz/start", headers=c["sh"], json={"course_id": c["course"]["id"], "clo_id": c["clo"]["id"]}).json()


def test_ten_questions_and_no_marks_until_submitted(client):
    c = ctx(client)
    q = start(client, c)
    assert q["total"] == 10 and len(q["questions"]) == 10 and "answer" not in q["questions"][0]  # no key before answering
    q0 = q["questions"][0]
    r = client.post(f"/api/quiz/attempts/{q['id']}/answer", headers=c["sh"], json={"item_id": q0["id"], "choice": wrong(q0)}).json()
    assert r == {"saved": True, "answered": 1, "total": 10}  # saved, but not marked: no correctness or explanation
    # the student may change their mind before submitting
    again = client.post(f"/api/quiz/attempts/{q['id']}/answer", headers=c["sh"], json={"item_id": q0["id"], "choice": right(q0)})
    assert again.status_code == 200 and again.json()["answered"] == 1
    view = client.get(f"/api/quiz/attempts/{q['id']}", headers=c["sh"]).json()
    assert view["questions"][0]["response"] == {"choice": right(q0)} and "answer" not in view["questions"][0]
    for x in q["questions"][1:]:
        client.post(f"/api/quiz/attempts/{q['id']}/answer", headers=c["sh"], json={"item_id": x["id"], "choice": wrong(x)})
    done = client.post(f"/api/quiz/attempts/{q['id']}/finish", headers=c["sh"]).json()
    first = next(x for x in done["questions"] if x["id"] == q0["id"])
    assert first["answer"]["correct"] is True and first["answer"]["correct_index"] == right(q0) and first["answer"]["explanation"]
    assert done["correct"] == 1 and done["score_pct"] == 10
    assert client.post(f"/api/quiz/attempts/{q['id']}/answer", headers=c["sh"], json={"item_id": q0["id"], "choice": 0}).status_code == 409


def test_finish_requires_all_answers(client):
    c = ctx(client)
    q = start(client, c)
    assert client.post(f"/api/quiz/attempts/{q['id']}/finish", headers=c["sh"]).status_code == 409


def test_completion_creates_clo_evidence_and_mastery(client):
    c = ctx(client)
    r = take_quiz(client, c["sh"], c["course"]["id"], c["clo"]["id"], 8, out_of=10)
    assert r["score_pct"] == 80 and r["mastery"]["attained"] is True and r["mastery"]["threshold"] == 60
    view = client.get(f"/api/mastery/course/{c['course']['id']}/student/{c['student'].id}", headers=c["ih"]).json()
    clo = view["clos"][0]
    assert clo["evidence"][0]["source"] == "quiz" and clo["evidence"][0]["correct"] == 8
    assert clo["current"]["evidence_ids"] == [clo["evidence"][0]["id"]]


def test_retake_uses_unseen_questions_first(client):
    c = ctx(client, per_clo=20)
    first = take_quiz(client, c["sh"], c["course"]["id"], c["clo"]["id"], 1, out_of=10)
    second = start(client, c)
    assert not {q["id"] for q in first["questions"]} & {q["id"] for q in second["questions"]}


def test_only_approved_items_and_minimum(client):
    c = active_course_with_student(client)
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=5, approve=False)
    walk_to_quiz(client, c["sh"], c["course"]["id"], c["course"]["clos"][0]["id"])  # quizzes open in path order: start the path first
    r = client.post("/api/quiz/start", headers=c["sh"], json={"course_id": c["course"]["id"], "clo_id": c["course"]["clos"][0]["id"]})
    assert r.status_code == 409


def test_student_mastery_overview(client):
    c = ctx(client)
    take_quiz(client, c["sh"], c["course"]["id"], c["clo"]["id"], 4, out_of=10)
    rows = client.get(f"/api/quiz/mastery/{c['course']['id']}", headers=c["sh"]).json()
    assert rows[0]["mastery_pct"] == 40 and rows[0]["attained"] is False and rows[1]["mastery_pct"] is None


def test_other_students_cannot_access_attempt(client):
    from .helpers import login, make_user

    c = ctx(client)
    q = start(client, c)
    other = make_user("student")
    assert client.get(f"/api/quiz/attempts/{q['id']}", headers=login(client, other.email)).status_code == 404


WRITTEN = [
    {"type": "true_false", "stem": "True or false: a variable stores a value in memory.", "answer": "true", "explanation": "It does."},
    {"type": "fill_blank", "stem": "The operator that assigns a value is _____.", "answer": "=", "accepted": ["equals sign"],
     "explanation": "= assigns."},
    {"type": "short_answer", "stem": "Why do programs use variables?", "answer": "To store and reuse values by name.",
     "key_points": ["store values", "reuse by name"], "explanation": "Variables name stored values."},
]


def add_written(client, c, copies=2, approve=True):
    """Teacher adds written questions (true/false, fill in the blank, short answer) to CLO1's formative bank."""
    made = []
    for k in range(copies):
        for w in WRITTEN:
            r = client.post("/api/items", headers=c["ih"], json={**w, "stem": f"{w['stem']} (v{k})", "course_id": c["course"]["id"],
                                                                 "clo_id": c["clo"]["id"], "purpose": "formative"})
            assert r.status_code == 201, r.text
            if approve:
                client.post(f"/api/items/{r.json()['id']}/review", headers=c["ih"], json={"decision": "approve"})
            made.append(r.json())
    return made


def _marker(monkeypatch, marks):
    def gen(prompt, system=None, **kw):
        return llm.LLMResult(json.dumps({"marks": marks}), "gemini-test", 1, 1, 1)
    monkeypatch.setattr(llm, "generate", gen)


def answer_all(client, c, q, short="We store values and reuse them by name.", tf="false", fill="Equals sign"):
    for x in q["questions"]:
        body = {"item_id": x["id"]}
        if x["type"] == "mcq":
            body["choice"] = right(x)
        else:
            body["text"] = {"true_false": tf, "fill_blank": fill, "short_answer": short}[x["type"]]
        assert client.post(f"/api/quiz/attempts/{q['id']}/answer", headers=c["sh"], json=body).status_code == 200


def test_quiz_mixes_approved_written_questions_and_scores_all_ten(client, monkeypatch):
    c = ctx(client)
    add_written(client, c)
    q = start(client, c)
    types = [x["type"] for x in q["questions"]]
    n_written = len(types) - types.count("mcq")
    assert len(types) == 10 and 3 <= n_written <= 5  # 3-5 written questions, multiple choice for the rest
    tf = next((x for x in q["questions"] if x["type"] == "true_false"), None)
    if tf:
        assert tf["options"] == ["True", "False"] and "answer" not in tf
    _marker(monkeypatch, [{"index": i, "correct": True, "feedback": "Good - you named both ideas."} for i in range(5)])
    answer_all(client, c, q)
    done = client.post(f"/api/quiz/attempts/{q['id']}/finish", headers=c["sh"]).json()
    wrong_tf = sum(1 for x in done["questions"] if x["type"] == "true_false")  # every true/false was answered "false" (key: true)
    for x in done["questions"]:
        if x["type"] == "true_false":
            assert x["answer"]["correct"] is False and x["answer"]["model_answer"] == "True"
        if x["type"] == "fill_blank":
            assert x["answer"]["correct"] is True  # accepted synonym, case-insensitive
        if x["type"] == "short_answer":
            assert x["answer"]["correct"] is True and "both ideas" in x["answer"]["feedback"]
    assert done["total"] == 10 and done["correct"] == 10 - wrong_tf and done["score_pct"] == 10 * (10 - wrong_tf)
    ev = client.get(f"/api/mastery/course/{c['course']['id']}/student/{c['student'].id}", headers=c["ih"]).json()["clos"][0]["evidence"][0]
    assert ev["total"] == 10  # every question counts toward mastery


def test_only_teacher_approved_written_questions_reach_students(client):
    c = ctx(client)
    add_written(client, c, approve=False)  # drafts only
    q = start(client, c)
    assert all(x["type"] == "mcq" for x in q["questions"])  # pending written questions are never shown


def test_written_questions_are_formative_only_and_keys_are_validated(client):
    c = ctx(client)
    base = {"course_id": c["course"]["id"], "clo_id": c["clo"]["id"]}
    assert client.post("/api/items", headers=c["ih"], json={**WRITTEN[0], **base, "purpose": "diagnostic"}).status_code == 422
    assert client.post("/api/items", headers=c["ih"], json={**base, "purpose": "formative", "type": "fill_blank",
                                                             "stem": "A sentence without any blank in it.", "answer": "x"}).status_code == 422
    it = client.post("/api/items", headers=c["ih"], json={**WRITTEN[2], **base, "purpose": "formative"}).json()
    assert it["type"] == "short_answer" and it["status"] == "pending" and it["key_points"] == ["store values", "reuse by name"]


def test_short_answers_marked_by_key_points_when_ai_is_down(client):
    c = ctx(client)
    add_written(client, c, copies=3)
    q = start(client, c)
    answer_all(client, c, q, short="They store values so we can reuse them by name", tf="true", fill="=")
    done = client.post(f"/api/quiz/attempts/{q['id']}/finish", headers=c["sh"]).json()
    sa = [x for x in done["questions"] if x["type"] == "short_answer"]
    assert all(x["answer"]["correct"] and "offline" in x["answer"]["feedback"] for x in sa)
    assert done["score_pct"] == 100


def test_true_false_rejects_other_text(client):
    c = ctx(client)
    add_written(client, c, copies=3)
    q = start(client, c)
    tf = next(x for x in q["questions"] if x["type"] == "true_false")
    assert client.post(f"/api/quiz/attempts/{q['id']}/answer", headers=c["sh"], json={"item_id": tf["id"], "text": "maybe"}).status_code == 422


def test_ai_drafts_written_questions_for_teacher_approval(client, monkeypatch):
    c = ctx(client)
    drafts = [{"type": "fill_blank", "stem": "The assignment operator in Python is _____.", "answer": "=", "explanation": "= assigns."},
              {"type": "short_answer", "stem": "Why is Python called dynamically typed?", "answer": "Values carry types, not names.",
               "key_points": ["values carry the type"], "explanation": "Types belong to values."}]
    monkeypatch.setattr(llm, "generate", lambda *a, **k: llm.LLMResult(json.dumps({"items": drafts}), "gemini-test", 1, 1, 1))
    r = client.post("/api/items/generate", headers=c["ih"], json={"course_id": c["course"]["id"], "clo_id": c["clo"]["id"],
                                                                   "purpose": "formative", "question_type": "written", "count": 2}).json()
    assert [x["type"] for x in r["created"]] == ["fill_blank", "short_answer"]
    assert all(x["status"] == "pending" for x in r["created"])  # the AI only drafts: a teacher must approve them
