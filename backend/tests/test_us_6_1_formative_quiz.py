"""US-6.1 Deliver formative quizzes and update mastery."""
from .helpers import walk_to_quiz
from .helpers import right, wrong
from .helpers import active_course_with_student, make_items, take_quiz


def ctx(client, per_clo=6):
    c = active_course_with_student(client)
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=per_clo)
    c["clo"] = c["course"]["clos"][0]
    return c


def test_immediate_feedback_after_each_answer(client):
    c = ctx(client)
    walk_to_quiz(client, c["sh"], c["course"]["id"], c["clo"]["id"])  # quizzes open in path order: start the path first
    q = client.post("/api/quiz/start", headers=c["sh"], json={"course_id": c["course"]["id"], "clo_id": c["clo"]["id"]}).json()
    assert q["total"] == 5 and "answer" not in q["questions"][0]  # no key before answering
    q0 = q["questions"][0]
    r = client.post(f"/api/quiz/attempts/{q['id']}/answer", headers=c["sh"], json={"item_id": q0["id"], "choice": wrong(q0)}).json()
    assert r["correct"] is False and r["correct_index"] == right(q0) and r["explanation"]  # index in the order shown
    again = client.post(f"/api/quiz/attempts/{q['id']}/answer", headers=c["sh"], json={"item_id": q0["id"], "choice": right(q0)})
    assert again.status_code == 409
    view = client.get(f"/api/quiz/attempts/{q['id']}", headers=c["sh"]).json()
    assert view["questions"][0]["answer"]["correct"] is False and "answer" not in view["questions"][1]


def test_finish_requires_all_answers(client):
    c = ctx(client)
    walk_to_quiz(client, c["sh"], c["course"]["id"], c["clo"]["id"])  # quizzes open in path order: start the path first
    q = client.post("/api/quiz/start", headers=c["sh"], json={"course_id": c["course"]["id"], "clo_id": c["clo"]["id"]}).json()
    assert client.post(f"/api/quiz/attempts/{q['id']}/finish", headers=c["sh"]).status_code == 409


def test_completion_creates_clo_evidence_and_mastery(client):
    c = ctx(client)
    r = take_quiz(client, c["sh"], c["course"]["id"], c["clo"]["id"], 4)
    assert r["score_pct"] == 80 and r["mastery"]["attained"] is True and r["mastery"]["threshold"] == 60
    view = client.get(f"/api/mastery/course/{c['course']['id']}/student/{c['student'].id}", headers=c["ih"]).json()
    clo = view["clos"][0]
    assert clo["evidence"][0]["source"] == "quiz" and clo["evidence"][0]["correct"] == 4
    assert clo["current"]["evidence_ids"] == [clo["evidence"][0]["id"]]


def test_retake_uses_unseen_questions_first(client):
    c = ctx(client, per_clo=10)
    first = take_quiz(client, c["sh"], c["course"]["id"], c["clo"]["id"], 1)
    walk_to_quiz(client, c["sh"], c["course"]["id"], c["clo"]["id"])  # quizzes open in path order: start the path first
    second = client.post("/api/quiz/start", headers=c["sh"], json={"course_id": c["course"]["id"], "clo_id": c["clo"]["id"]}).json()
    assert not {q["id"] for q in first["questions"]} & {q["id"] for q in second["questions"]}


def test_only_approved_items_and_minimum(client):
    c = active_course_with_student(client)
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=5, approve=False)
    walk_to_quiz(client, c["sh"], c["course"]["id"], c["course"]["clos"][0]["id"])  # quizzes open in path order: start the path first
    r = client.post("/api/quiz/start", headers=c["sh"], json={"course_id": c["course"]["id"], "clo_id": c["course"]["clos"][0]["id"]})
    assert r.status_code == 409


def test_student_mastery_overview(client):
    c = ctx(client)
    take_quiz(client, c["sh"], c["course"]["id"], c["clo"]["id"], 2)
    rows = client.get(f"/api/quiz/mastery/{c['course']['id']}", headers=c["sh"]).json()
    assert rows[0]["mastery_pct"] == 40 and rows[0]["attained"] is False and rows[1]["mastery_pct"] is None


def test_other_students_cannot_access_attempt(client):
    from .helpers import login, make_user

    c = ctx(client)
    walk_to_quiz(client, c["sh"], c["course"]["id"], c["clo"]["id"])  # quizzes open in path order: start the path first
    q = client.post("/api/quiz/start", headers=c["sh"], json={"course_id": c["course"]["id"], "clo_id": c["clo"]["id"]}).json()
    other = make_user("student")
    assert client.get(f"/api/quiz/attempts/{q['id']}", headers=login(client, other.email)).status_code == 404
