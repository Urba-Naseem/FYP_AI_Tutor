"""Integration test: the demo seed drives the whole pilot (every sprint) through the real API."""
from app.db import SessionLocal
from app.models import Appeal, ContentVersion, Escalation, LearningPath, MasteryRecord, User
from app.seed import DEMO_PASSWORD, USERS, run


def test_seed_builds_complete_pilot(client):
    run()
    db = SessionLocal()
    try:
        assert db.query(User).count() == len(USERS) == 12
        assert db.query(ContentVersion).filter(ContentVersion.status == "approved").count() >= 4
        assert db.query(LearningPath).filter(LearningPath.status == "active").count() == 2  # Ayesha + Bilal
        assert db.query(MasteryRecord).filter(MasteryRecord.is_current.is_(True), MasteryRecord.attained.is_(True)).count() >= 2
        assert db.query(Escalation).count() == 1 and db.query(Appeal).count() == 1
    finally:
        db.close()
    r = client.post("/api/auth/login", json={"email": "sara.instructor@university.edu", "password": DEMO_PASSWORD})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    ov = client.get("/api/console/overview", headers=h).json()
    cs101 = next(c for c in ov if c["course"]["code"] == "CS101")
    assert cs101["enrolled"] == 3 and cs101["needs_intervention"] >= 1 and cs101["pending_total"] >= 1
    assert client.get("/api/audit/verify", headers=h).status_code == 403  # instructors cannot read the audit log
