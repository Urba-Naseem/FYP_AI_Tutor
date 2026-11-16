"""US-5.1 Deliver accessible unit material."""
from .helpers import active_course_with_student, approved_lessons, login, make_user


def test_students_see_only_approved_versions(client):
    c = active_course_with_student(client)
    cid = c["course"]["id"]
    docs = approved_lessons(client, c["ih"], c["course"])
    unit = c["course"]["units"][0]
    doc_id = docs[unit["id"]]["id"]
    client.post(f"/api/content/{doc_id}/versions", headers=c["ih"], json={"body_md": "# Draft v2 not yet approved\n\nSecret draft text here.", "change_summary": "wip"})
    u = client.get(f"/api/learn/{cid}/units/{unit['id']}", headers=c["sh"]).json()
    assert u["lesson"]["version_no"] == 1 and "Secret draft" not in u["lesson"]["body_md"]
    assert "$a^2+b^2=c^2$" in u["lesson"]["body_md"] and "```python" in u["lesson"]["body_md"]
    listing = client.get(f"/api/learn/{cid}/units", headers=c["sh"]).json()
    assert all(x["available"] for x in listing) and listing[0]["clos"]


def test_unit_without_approved_content(client):
    c = active_course_with_student(client)
    u = client.get(f"/api/learn/{c['course']['id']}/units/{c['course']['units'][0]['id']}", headers=c["sh"]).json()
    assert u["lesson"] is None


def test_requires_enrolment(client):
    c = active_course_with_student(client)
    other = make_user("student")
    r = client.get(f"/api/learn/{c['course']['id']}/units", headers=login(client, other.email))
    assert r.status_code == 403


def test_report_error_routes_to_instructor_and_resolves(client):
    c = active_course_with_student(client)
    docs = approved_lessons(client, c["ih"], c["course"])
    unit = c["course"]["units"][0]
    vid = client.get(f"/api/learn/{c['course']['id']}/units/{unit['id']}", headers=c["sh"]).json()["lesson"]["version_id"]
    assert client.post(f"/api/learn/versions/{vid}/report", headers=c["sh"], json={"message": "x"}).status_code == 422
    r = client.post(f"/api/learn/versions/{vid}/report", headers=c["sh"], json={"category": "accessibility", "message": "Diagram has no description", "excerpt": "graph TD"})
    assert r.status_code == 201
    tasks = client.get("/api/reviews?kind=content_error", headers=c["ih"]).json()
    assert len(tasks) == 1
    reports = client.get(f"/api/content-reports?course_id={c['course']['id']}", headers=c["ih"]).json()
    assert reports[0]["category"] == "accessibility"
    client.post(f"/api/content-reports/{reports[0]['id']}/resolve", headers=c["ih"], json={"resolution": "Added alt text"})
    assert client.get("/api/reviews?kind=content_error", headers=c["ih"]).json() == []
    assert docs


def test_completing_study_step_updates_path(client):
    c = active_course_with_student(client)
    p = client.post(f"/api/paths/me/{c['course']['id']}/generate", headers=c["sh"]).json()
    learn_step = next(s for s in p["steps"] if s["step_type"] == "learn")
    quiz_step = next(s for s in p["steps"] if s["step_type"] == "practice")
    assert client.post(f"/api/learn/steps/{learn_step['id']}/complete", headers=c["sh"]).status_code == 200
    assert client.post(f"/api/learn/steps/{quiz_step['id']}/complete", headers=c["sh"]).status_code == 409
    p = client.get(f"/api/paths/me/{c['course']['id']}", headers=c["sh"]).json()
    assert p["progress"]["done"] == 1


def test_views_logged_as_learning_events(client):
    c = active_course_with_student(client)
    approved_lessons(client, c["ih"], c["course"])
    client.get(f"/api/learn/{c['course']['id']}/units/{c['course']['units'][0]['id']}", headers=c["sh"])
    assert client.get("/api/audit?action=learn.unit_viewed", headers=c["ah"]).json()["total"] == 1
