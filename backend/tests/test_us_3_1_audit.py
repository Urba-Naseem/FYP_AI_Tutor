"""US-3.1 Capture learning and audit events."""
import pytest
from sqlalchemy import text

from app.core.audit import learning, record, verify_chain
from app.db import SessionLocal
from app.models.audit import AuditEvent

from .helpers import as_role, make_user


def test_login_and_admin_actions_are_recorded_and_attributable(client):
    admin, h = as_role(client, "admin")
    client.post("/api/users", headers=h, json={"email": "t1@university.edu", "full_name": "T", "role": "instructor", "password": "Passw0rd!2026"})
    items = client.get("/api/audit", headers=h).json()["items"]
    actions = [i["action"] for i in items]
    assert "auth.login" in actions and "user.created" in actions
    created = next(i for i in items if i["action"] == "user.created")
    assert created["actor_id"] == admin.id and created["actor_role"] == "admin"
    assert created["entity_type"] == "user" and created["request_id"]


def test_denied_access_is_logged(client):
    _, sh = as_role(client, "student")
    client.get("/api/users", headers=sh)
    _, ah = as_role(client, "admin")
    items = client.get("/api/audit?action=authz.denied", headers=ah).json()["items"]
    assert items and items[0]["category"] == "security"


def test_audit_search_filters(client):
    _, h = as_role(client, "admin")
    r = client.get("/api/audit?category=security&limit=5", headers=h).json()
    assert all(i["category"] == "security" for i in r["items"])


def test_exam_staff_can_read_audit_but_students_cannot(client):
    _, eh = as_role(client, "exam_staff")
    _, sh = as_role(client, "student")
    assert client.get("/api/audit", headers=eh).status_code == 200
    assert client.get("/api/audit", headers=sh).status_code == 403


def test_events_are_append_only_orm_and_sql(client):
    db = SessionLocal()
    record(db, None, "test.event", "thing", 1, {"a": 1})
    db.commit()
    ev = db.query(AuditEvent).first()
    ev.action = "tampered"
    with pytest.raises(PermissionError):
        db.commit()
    db.rollback()
    with pytest.raises(Exception):
        db.execute(text("UPDATE audit_events SET action='x'"))
        db.commit()
    db.rollback()
    with pytest.raises(Exception):
        db.execute(text("DELETE FROM audit_events"))
        db.commit()
    db.close()


def test_hash_chain_detects_tampering(client):
    db = SessionLocal()
    u = make_user("student")
    for i in range(3):
        learning(db, u, "page_view", "unit", i, {"n": i})
    db.commit()
    assert verify_chain(db) == {"valid": True, "checked": 3, "forks": []}
    # bypass the trigger to simulate an attacker editing the database file
    on_table = " ON audit_events" if db.get_bind().dialect.name == "postgresql" else ""
    db.execute(text(f"DROP TRIGGER audit_no_update{on_table}"))
    db.execute(text("UPDATE audit_events SET details='{\"n\": 99}' WHERE id=2"))
    db.commit()
    result = verify_chain(db)
    assert result["valid"] is False and result["broken_at"] == 2
    db.close()


def test_learning_events_have_learning_category(client):
    db = SessionLocal()
    u = make_user("student")
    ev = learning(db, u, "quiz_answer", "item", 5, {"correct": True}, course_id=3)
    db.commit()
    assert ev.category == "learning" and ev.action == "learn.quiz_answer" and ev.course_id == 3
    db.close()


def test_two_requests_logging_at_once_keep_one_chain(client):
    """Two sessions both read the same last event before either commits (e.g. a page that sends its request twice).
    The second commit must link to the first one's event instead of forking the chain."""
    a, b = SessionLocal(), SessionLocal()
    u = make_user("student")
    record(a, u, "student.email_verified", "user", u.id, {})
    record(b, u, "student.email_verified", "user", u.id, {})
    b.commit()
    a.commit()
    a.close(), b.close()
    db = SessionLocal()
    assert verify_chain(db)["valid"] is True
    db.close()


def test_audit_search_by_record_number(client):
    db = SessionLocal()
    u = make_user("student")
    evs = [learning(db, u, "page_view", "unit", i, {"n": i}) for i in range(5)]
    db.commit()
    ids = [e.id for e in evs]
    db.close()
    _, ah = as_role(client, "admin")
    one = client.get(f"/api/audit?record_id={ids[2]}", headers=ah).json()
    assert one["total"] == 1 and one["items"][0]["id"] == ids[2]
    rng = client.get(f"/api/audit?record_from={ids[1]}&record_to={ids[3]}", headers=ah).json()
    assert {e["id"] for e in rng["items"]} >= {ids[1], ids[2], ids[3]} and all(ids[1] <= e["id"] <= ids[3] for e in rng["items"])


def test_events_flushed_early_still_chain_in_order(client):
    """A long request writes other rows (flush) before its commit while another session commits audit events in
    between: the chain must still be one line (no fork)."""
    a, b = SessionLocal(), SessionLocal()
    u = make_user("student")
    record(a, u, "student.long_request", "user", u.id, {})
    a.flush()  # early flush of the long request
    record(b, u, "student.background_job", "user", u.id, {})
    b.commit()
    a.commit()
    a.close(), b.close()
    db = SessionLocal()
    r = verify_chain(db)
    assert r["valid"] is True and r["forks"] == []
    db.close()


def test_fork_is_reported_but_tampering_still_breaks(client):
    db = SessionLocal()
    u = make_user("student")
    for i in range(3):
        learning(db, u, "page_view", "unit", i, {"n": i})
    db.commit()
    on_table = " ON audit_events" if db.get_bind().dialect.name == "postgresql" else ""
    ids = [e.id for e in db.query(AuditEvent).order_by(AuditEvent.id)]
    first = db.get(AuditEvent, ids[0])
    # simulate an old fork: record 3 links to record 1 (contents rehashed, so intact)
    db.execute(text(f"DROP TRIGGER audit_no_update{on_table}"))
    from app.core.audit import _digest

    third = db.get(AuditEvent, ids[2])
    new_hash = _digest(first.hash, third)  # contents unchanged, only the link points further back
    db.execute(text("UPDATE audit_events SET prev_hash=:p, hash=:h WHERE id=:i"), {"p": first.hash, "h": new_hash, "i": ids[2]})
    db.commit()
    db.expire_all()
    r = verify_chain(db)
    assert r["valid"] is True and r["forks"] == [ids[2]]
    db.execute(text("UPDATE audit_events SET details='{\"n\": 99}' WHERE id=:i"), {"i": ids[1]})  # real tampering
    db.commit()
    assert verify_chain(db)["valid"] is False
    db.close()
