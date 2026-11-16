"""US-10.1 Show student progress."""
from .helpers import login, make_items, make_user, published_diagnostic, take_diagnostic, take_quiz


def ctx(client):
    c = published_diagnostic(client)
    make_items(client, c["ih"], c["course"], purpose="formative", per_clo=10)
    take_diagnostic(client, c, correct_clos={"CLO1"})
    c["cid"] = c["course"]["id"]
    clos = {o["code"]: o for o in c["course"]["clos"]}
    take_quiz(client, c["sh"], c["cid"], clos["CLO1"]["id"], 5)  # 100% attained
    take_quiz(client, c["sh"], c["cid"], clos["CLO2"]["id"], 1)  # 20% weak
    take_quiz(client, c["sh"], c["cid"], clos["CLO3"]["id"], 2)  # 40% weak
    return c


def test_progress_shows_attainment_weak_areas_and_next_steps(client):
    c = ctx(client)
    p = client.get(f"/api/progress/me/{c['cid']}", headers=c["sh"]).json()
    assert p["summary"]["attained"] == 1 and p["summary"]["total"] == 5
    st = {r["clo_code"]: r["status"] for r in p["clos"]}
    assert st == {"CLO1": "attained", "CLO2": "below", "CLO3": "below", "CLO4": "not_assessed", "CLO5": "not_assessed"}
    assert [w["clo_code"] for w in p["weak_areas"]][:2] == ["CLO2", "CLO3"]  # biggest gap first
    assert p["next_steps"] and p["advice"][0].startswith("CLO2")


def test_download_pdf_and_csv(client):
    c = ctx(client)
    pdf = client.get(f"/api/progress/me/{c['cid']}/download?format=pdf", headers=c["sh"])
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and "attachment" in pdf.headers["content-disposition"]
    csv = client.get(f"/api/progress/me/{c['cid']}/download?format=csv", headers=c["sh"])
    text = csv.content.decode("utf-8-sig")
    assert "CLO1" in text and "attained" in text and "Next steps" in text
    assert client.get(f"/api/progress/me/{c['cid']}/download?format=xls", headers=c["sh"]).status_code == 422


def test_staff_view_and_privacy(client):
    c = ctx(client)
    assert client.get(f"/api/progress/course/{c['cid']}/student/{c['student'].id}", headers=c["ih"]).json()["summary"]["attained"] == 1
    other = make_user("student")
    assert client.get(f"/api/progress/me/{c['cid']}", headers=login(client, other.email)).status_code == 403
    outsider = make_user("instructor")
    assert client.get(f"/api/progress/course/{c['cid']}/student/{c['student'].id}", headers=login(client, outsider.email)).status_code == 403
