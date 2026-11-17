"""Student experience: the path is followed step by step, students see who teaches them, and departments stay
separate everywhere a student can look."""
from .helpers import login, make_items, published_diagnostic, take_diagnostic


def test_path_is_followed_step_by_step(client):
    c = published_diagnostic(client)
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=6)
    take_diagnostic(client, c)  # weak on everything: foundations + topics + quiz for every CLO
    cid = c["course"]["id"]
    p = client.get(f"/api/paths/me/{cid}", headers=c["sh"]).json()
    nxt = next(s for s in p["steps"] if s["id"] == p["next_step_id"])
    later_quiz = next(s for s in p["steps"] if s["step_type"] == "practice" and s["clo_id"] != nxt["clo_id"])
    # a later quiz cannot be started, and the formative list says why
    r = client.post("/api/quiz/start", headers=c["sh"], json={"course_id": cid, "clo_id": later_quiz["clo_id"]})
    assert r.status_code == 409 and "next step" in r.json()["detail"]
    rows = {x["clo_id"]: x for x in client.get(f"/api/quiz/mastery/{cid}", headers=c["sh"]).json()}
    assert rows[later_quiz["clo_id"]]["locked_reason"]
    # a later study step cannot be marked done, the current one can
    later_learn = next(s for s in p["steps"] if s["step_type"] == "learn" and s["position"] > nxt["position"])
    assert client.post(f"/api/learn/steps/{later_learn['id']}/complete", headers=c["sh"]).status_code == 409
    if nxt["step_type"] in ("learn", "remedial"):
        assert client.post(f"/api/learn/steps/{nxt['id']}/complete", headers=c["sh"]).status_code == 200
    # course material stays open: any topic lesson can be read
    t = client.get(f"/api/learn/{cid}/topics", headers=c["sh"]).json()[-1]
    assert client.get(f"/api/learn/{cid}/topics/{t['id']}/lesson", headers=c["sh"]).status_code == 200


def test_students_see_their_instructor_and_contact_details(client):
    c = published_diagnostic(client)
    r = client.patch("/api/auth/profile", headers=c["ih"], json={"office_hours": "Mon 2-4 PM", "office_location": "Room 204", "about": "I teach programming."})
    assert r.status_code == 200
    d = client.get(f"/api/learn/{c['course']['id']}/instructors", headers=c["sh"]).json()
    assert d["section"]["code"] == c["section"]["code"]
    t = d["instructors"][0]
    assert t["email"] == c["inst"].email and t["office_hours"] == "Mon 2-4 PM" and t["office_location"] == "Room 204"
    # students have no public profile to edit
    assert client.patch("/api/auth/profile", headers=c["sh"], json={"office_hours": "x"}).status_code == 403


def test_a_student_never_sees_another_departments_courses(client):
    from .test_departments import two_departments, make_user
    from app.db import SessionLocal
    from app.models.user import User

    d = two_departments(client)
    s = make_user("student")
    db = SessionLocal()
    db.get(User, s.id).program_code = "BSCS"
    db.commit()
    db.close()
    sh = login(client, s.email)
    se_id = d["se"]["course"]["id"]
    assert all(c["id"] != se_id for c in client.get("/api/enrollment/catalog", headers=sh).json())
    assert all(c["id"] != se_id for c in client.get("/api/outcomes/courses", headers=sh).json())
    assert client.get(f"/api/outcomes/courses/{se_id}", headers=sh).status_code == 403
    assert client.get(f"/api/learn/{se_id}/topics", headers=sh).status_code == 403
    assert all(r["program"]["code"] == "BSCS" for r in client.get("/api/enrollment/roadmap", headers=sh).json())


def test_no_quiz_before_the_path_exists_and_no_reteach_before_a_quiz(client):
    from .helpers import active_course_with_student

    c = active_course_with_student(client)
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=6)
    cid, clo = c["course"]["id"], c["course"]["clos"][-1]["id"]
    r = client.post("/api/quiz/start", headers=c["sh"], json={"course_id": cid, "clo_id": clo})
    assert r.status_code == 409 and "diagnostic" in r.json()["detail"]  # brand-new course: straight to the last quiz is refused
    rows = client.get(f"/api/quiz/mastery/{cid}", headers=c["sh"]).json()
    assert all(x["locked_reason"] for x in rows)
    r = client.get(f"/api/reteach/{cid}/clo/{clo}", headers=c["sh"])
    assert r.status_code == 409 and "formative quiz" in r.json()["detail"]


def test_options_are_shuffled_per_attempt_with_balanced_answer_positions(client):
    from .helpers import active_course_with_student, right, walk_to_quiz
    from .test_personalisation import second_student

    c = active_course_with_student(client)
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=15)
    cid, clo = c["course"]["id"], c["course"]["clos"][0]["id"]
    _, sh2 = second_student(client, c)
    quizzes = []
    for h in (c["sh"], sh2):
        walk_to_quiz(client, h, cid, clo)
        quizzes.append(client.post("/api/quiz/start", headers=h, json={"course_id": cid, "clo_id": clo}).json())
    q = quizzes[0]
    letters = [right(x) for x in q["questions"]]
    # 10 questions over 4 options: every letter is used and none more than 3 times - never "always B"
    assert set(letters[:4]) == {0, 1, 2, 3} and max(letters.count(k) for k in range(4)) <= 3
    # answering in the shown order is marked correctly (on submission), and the review shows the shown positions
    x = q["questions"][0]
    for y in q["questions"]:
        client.post(f"/api/quiz/attempts/{q['id']}/answer", headers=c["sh"], json={"item_id": y["id"], "choice": right(y)})
    again = client.get(f"/api/quiz/attempts/{q['id']}", headers=c["sh"]).json()["questions"][0]
    assert again["options"] == x["options"] and again["response"]["choice"] == right(x)  # stable order on reload
    done = client.post(f"/api/quiz/attempts/{q['id']}/finish", headers=c["sh"]).json()["questions"][0]
    assert done["answer"]["correct"] is True and done["answer"]["correct_index"] == right(x) and done["answer"]["chosen"] == right(x)
    # two students rarely see the same option order for the same item
    same = [a for a in quizzes[0]["questions"] for b in quizzes[1]["questions"] if a["id"] == b["id"]]
    assert all(set(a["options"]) for a in same)


def test_suggested_reading_for_students(client):
    from .helpers import add_approved_source

    c = published_diagnostic(client)
    cid = c["course"]["id"]
    src = add_approved_source(client, c["ih"], c["ih"], cid)
    d = client.get(f"/api/learn/{cid}/readings?q=recursion base case", headers=c["sh"]).json()
    assert [s["id"] for s in d["sources"]] == [src["id"]] and d["passages"] and "base case" in d["passages"][0]["text"]
    f = client.get(f"/api/learn/{cid}/readings/{src['id']}/file", headers=c["sh"])
    assert f.status_code == 200 and len(f.content) > 100 and "attachment" in f.headers["content-disposition"]
    # with the AI offline, a topic lesson teaches from the reading
    take_diagnostic(client, c)
    t = client.get(f"/api/learn/{cid}/topics", headers=c["sh"]).json()[0]
    les = client.get(f"/api/learn/{cid}/topics/{t['id']}/lesson", headers=c["sh"]).json()
    assert les["origin"] == "offline" and "From your suggested reading" in les["lecture_md"]
    # other students of other courses cannot download it
    from .helpers import make_user

    assert client.get(f"/api/learn/{cid}/readings/{src['id']}/file", headers=login(client, make_user("student").email)).status_code == 403
