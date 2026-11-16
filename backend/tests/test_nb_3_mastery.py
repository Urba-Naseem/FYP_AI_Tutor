"""NB-3 Define mastery calculation and work/test evidence versioning."""
import time

from app.db import SessionLocal
from app.models.outcomes import CLO
from app.models.user import User
from app.services import mastery

from .helpers import approve_curriculum, as_role, make_user


def items(n_correct, n=5):
    return [{"item_id": i, "item_version": 1, "correct": i < n_correct} for i in range(n)]


def setup(client):
    _, h = as_role(client, "admin")
    courses = approve_curriculum(client, h)
    student = make_user("student")
    clo_id = courses["CS101"]["clos"][0]["id"]
    return h, courses, student, clo_id


def test_best_of_last_three_attempts(client):
    h, courses, student, clo_id = setup(client)
    db = SessionLocal()
    s = db.get(User, student.id)
    scores = []
    for correct in (2, 4, 1, 1, 2):  # 40, 80, 20, 20, 40
        _, rec = mastery.add_evidence(db, s, clo_id, "quiz", "quiz_attempt:x", items(correct))
        scores.append((rec.mastery_pct, rec.attained))
        time.sleep(0.002)
    db.commit()
    # after 2nd attempt best=80 (attained); after 5th, window is [40,20,20] -> 40 (not attained)
    assert scores[1] == (80.0, True)
    assert scores[3] == (80.0, True)
    assert scores[4] == (40.0, False)
    db.close()


def test_record_stores_criterion_version_rule_and_evidence(client):
    h, courses, student, clo_id = setup(client)
    db = SessionLocal()
    e, rec = mastery.add_evidence(db, db.get(User, student.id), clo_id, "quiz", "quiz_attempt:1", items(3))
    db.commit()
    assert rec.threshold == 60 and rec.criterion_version == 1 and rec.rule_version == mastery.RULE_VERSION
    assert rec.evidence_ids == [e.id] and rec.attained is True
    db.close()


def test_criteria_change_applies_prospectively_and_recalc_is_explicit(client):
    h, courses, student, clo_id = setup(client)
    db = SessionLocal()
    mastery.add_evidence(db, db.get(User, student.id), clo_id, "quiz", "quiz_attempt:1", items(3))  # 60%
    db.commit()
    db.close()
    client.post("/api/criteria", headers=h, json={"scope_type": "course", "ref_id": courses["CS101"]["id"], "threshold": 70, "reason": "Raised"})
    sid = student.id
    view = client.get(f"/api/mastery/course/{courses['CS101']['id']}/student/{sid}", headers=h).json()
    assert view["clos"][0]["current"]["attained"] is True  # decision unchanged
    r = client.post("/api/mastery/recalculate", headers=h, json={"course_id": courses["CS101"]["id"], "mode": "reproduce", "reason": "Audit check"})
    assert r.json() == {"records": 1, "changed": 0, "mode": "reproduce"}
    r = client.post("/api/mastery/recalculate", headers=h, json={"course_id": courses["CS101"]["id"], "mode": "current_criteria", "reason": "Apply new policy"})
    assert r.json()["changed"] == 1
    view = client.get(f"/api/mastery/course/{courses['CS101']['id']}/student/{sid}", headers=h).json()
    cur = view["clos"][0]["current"]
    assert cur["attained"] is False and cur["threshold"] == 70 and cur["trigger"] == "recalculation"
    assert len(view["clos"][0]["history"]) == 3
    assert client.get("/api/audit?action=mastery.recalculated", headers=h).json()["total"] == 2


def test_amendment_creates_new_evidence_version(client):
    h, courses, student, clo_id = setup(client)
    db = SessionLocal()
    admin = db.query(User).filter(User.role == "admin").first()
    e, rec = mastery.add_evidence(db, db.get(User, student.id), clo_id, "quiz", "quiz_attempt:1", items(2))
    assert rec.attained is False
    new, rec2 = mastery.amend_evidence(db, e.id, 4, "Item 3 key was wrong", admin)
    db.commit()
    assert db.get(type(e), e.id).status == "superseded"
    assert new.version_no == 2 and new.supersedes_id == e.id and rec2.attained is True and rec2.trigger == "amendment"
    db.close()


def test_diagnostic_is_not_mastery_evidence(client):
    h, courses, student, clo_id = setup(client)
    db = SessionLocal()
    try:
        mastery.add_evidence(db, db.get(User, student.id), clo_id, "diagnostic", "d:1", items(5))
        assert False
    except ValueError:
        pass
    db.close()


def test_recalc_requires_permission_and_reason(client):
    h, courses, *_ = setup(client)
    assert client.post("/api/mastery/recalculate", headers=h, json={"course_id": courses["CS101"]["id"], "reason": ""}).status_code == 422
    _, ih = as_role(client, "instructor")
    assert client.post("/api/mastery/recalculate", headers=ih, json={"course_id": courses["CS101"]["id"], "reason": "trying"}).status_code == 403
    assert client.get("/api/mastery/policy", headers=ih).json()["rule_version"] == "best-of-last-3/v1"
