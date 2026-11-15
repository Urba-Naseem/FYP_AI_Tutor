"""US-4.5 - Adapt the path after assessment using stable decision rules (adapt-rules/v1).

Inputs: the new mastery decision for the CLO (mastery %, attained, threshold), the
type of the step the quiz belonged to, and how many of the learner's most recent
attempts for that CLO in a row were below the threshold.

  A-ADVANCE      attained                                  -> quiz step done, move on
  A-REVIEW-FAIL  not attained on a "quick check" (review)  -> insert study + practice
  A-REPEAT       not attained, mastery >= 40%, fewer than
                 2 failures in a row                       -> insert another practice (new questions)
  A-REMEDIAL     otherwise (mastery < 40% or repeated
                 failure)                                  -> insert a reteach step (US-6.3)
  A-LOCKED       path locked by an instructor              -> no change (logged)

The same inputs always give the same decision. Each change creates a new path
version (history for US-4.4) and a PathDecision record.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.audit import learning
from ..db import utcnow
from ..models.mastery import MasteryRecord
from ..models.outcomes import CLO
from ..models.paths import PathDecision
from . import paths as path_svc

RULES_VERSION = "adapt-rules/v1"
NEAR = 40.0
QUIZ_STEPS = ("practice", "review", "reteach")


def consecutive_failures(db: Session, student_id: int, clo_id: int) -> int:
    recs = db.scalars(select(MasteryRecord).where(MasteryRecord.student_id == student_id, MasteryRecord.clo_id == clo_id,
                                                  MasteryRecord.trigger == "attempt").order_by(MasteryRecord.id.desc())).all()
    n = 0
    for r in recs:
        if r.attained:
            break
        n += 1
    return n


def decide(attained: bool, mastery_pct: float, step_type: str | None, failures: int) -> tuple[str, str]:
    if attained:
        return "A-ADVANCE", "advance"
    if step_type == "review":
        return "A-REVIEW-FAIL", "review_fail"
    if mastery_pct >= NEAR and failures < 2:
        return "A-REPEAT", "repeat"
    return "A-REMEDIAL", "remedial"


def adapt_after_quiz(db: Session, student, attempt, rec: MasteryRecord) -> dict | None:
    path = path_svc.active_path(db, student.id, attempt.course_id)
    if not path:
        return None
    steps = path_svc.steps(db, path)
    clo = db.get(CLO, attempt.clo_id)
    # the step this quiz was for: the given step, else the first open quiz step for the CLO
    current = None
    if attempt.step_id:
        current = next((s for s in steps if s.id == attempt.step_id), None)
    if current is None:
        current = next((s for s in steps if s.clo_id == clo.id and s.step_type in QUIZ_STEPS and s.status == "pending"), None)
    failures = consecutive_failures(db, student.id, clo.id)
    inputs = {"mastery_pct": rec.mastery_pct, "attained": rec.attained, "threshold": rec.threshold,
              "step_type": current.step_type if current else None, "consecutive_failures": failures,
              "rules": RULES_VERSION}

    if path.locked:
        d = PathDecision(student_id=student.id, course_id=attempt.course_id, clo_id=clo.id, from_path_id=path.id, to_path_id=None,
                         rule="A-LOCKED", decision="locked", inputs=inputs, quiz_attempt_id=attempt.id,
                         message="Your instructor has fixed your path, so it was not changed automatically.")
        db.add(d)
        learning(db, student, "path_adaptation_blocked", "learning_path", path.id, inputs, course_id=attempt.course_id)
        return {"decision": d.decision, "rule": d.rule, "message": d.message}

    if attempt.kind == "reteach":  # US-6.3 advancement rules
        from .reteach import RULES_VERSION as RT, after_reteach_quiz

        if current is None or current.step_type != "reteach":
            current = next((s for s in steps if s.clo_id == clo.id and s.step_type == "reteach" and s.status == "pending"), current)
        res = after_reteach_quiz(db, student, attempt, rec, path, steps, current)
        inputs["rules"] = RT
        cyc = res["cycle"]
        if cyc:
            inputs.update(reteach_attempts=cyc.attempts_used, max_attempts=cyc.max_attempts)
        to_path = None
        if res["steps"] is not None:
            to_path = path_svc.save_version(db, student, student.id, attempt.course_id, res["steps"], path.summary,
                                            "adaptation", reason=f"{res['rule']}: {res['message']}").id
        db.add(PathDecision(student_id=student.id, course_id=attempt.course_id, clo_id=clo.id, from_path_id=path.id,
                            to_path_id=to_path, rule=res["rule"], decision=res["decision"], inputs=inputs,
                            message=res["message"], quiz_attempt_id=attempt.id))
        learning(db, student, "reteach_decision", "reteach_cycle", cyc.id if cyc else None,
                 {**inputs, "rule": res["rule"]}, course_id=attempt.course_id)
        if res["rule"] == "R-EXHAUSTED":
            try:  # US-6.4
                from .escalation import raise_escalation

                raise_escalation(db, student, attempt.course_id, clo, "reteach_exhausted",
                                 f"{clo.code} still below {rec.threshold:.0f}% after {inputs.get('max_attempts', 3)} reteach attempts "
                                 f"(mastery {rec.mastery_pct:.0f}%).")
            except ImportError:
                pass
        return {"decision": res["decision"], "rule": res["rule"], "message": res["message"]}

    rule, decision = decide(rec.attained, rec.mastery_pct, current.step_type if current else None, failures)
    new_steps = [path_svc.step_dict(s) for s in steps]
    idx = steps.index(current) if current else None
    thr = f"{rec.threshold:.0f}%"

    if decision == "advance":
        for d_, s in zip(new_steps, steps):
            if s.clo_id == clo.id and s.step_type in QUIZ_STEPS and s.status == "pending":
                d_["status"], d_["completed_at"] = "done", utcnow()
        msg = f"{clo.code} attained ({rec.mastery_pct:.0f}% ≥ {thr}). Moving on to your next step."
    else:
        if idx is not None:
            new_steps[idx]["status"], new_steps[idx]["completed_at"] = "done", utcnow()
            new_steps[idx]["meta"] = {**new_steps[idx]["meta"], "outcome": f"{rec.mastery_pct:.0f}% (below {thr})"}
            insert_at = idx + 1
            unit_id = steps[idx].unit_id
        else:
            insert_at = len(new_steps)
            unit_id = clo.unit_id
        if decision == "review_fail":
            extra = [dict(unit_id=unit_id, clo_id=clo.id, step_type="learn", rule=rule,
                          rationale=f"Your quick check on {clo.code} scored {rec.mastery_pct:.0f}% (below {thr}), so you study the material properly."),
                     dict(unit_id=unit_id, clo_id=clo.id, step_type="practice", rule=rule,
                          rationale=f"Then practise {clo.code} until you reach {thr}.")]
            msg = f"The quick check showed gaps in {clo.code}, so study and practice steps were added."
        elif decision == "repeat":
            extra = [dict(unit_id=unit_id, clo_id=clo.id, step_type="practice", rule=rule,
                          rationale=f"You scored {rec.mastery_pct:.0f}% on {clo.code} - close to {thr}. Another formative quiz with new questions.")]
            msg = f"You're close on {clo.code} ({rec.mastery_pct:.0f}%). Try another formative quiz with new questions."
        else:
            extra = [dict(unit_id=unit_id, clo_id=clo.id, step_type="reteach", rule=rule,
                          rationale=(f"{clo.code} is at {rec.mastery_pct:.0f}% after {failures} attempt(s) below {thr}. "
                                     "You get a different explanation and fresh questions."))]
            msg = f"Let's look at {clo.code} a different way: a reteach step with a new explanation was added."
        for e in extra:
            e.update(status="pending", meta={"inserted_by": rule})
        new_steps[insert_at:insert_at] = extra

    new_path = path_svc.save_version(db, student, student.id, attempt.course_id, new_steps, path.summary, "adaptation",
                                     reason=f"{rule}: {msg}")
    d = PathDecision(student_id=student.id, course_id=attempt.course_id, clo_id=clo.id, from_path_id=path.id,
                     to_path_id=new_path.id, rule=rule, decision=decision, inputs=inputs, message=msg, quiz_attempt_id=attempt.id)
    db.add(d)
    learning(db, student, "path_adapted", "learning_path", new_path.id, {**inputs, "rule": rule, "decision": decision},
             course_id=attempt.course_id)
    try:  # US-6.4: repeated difficulty raises an escalation
        from .escalation import check_escalation

        check_escalation(db, student, attempt.course_id, clo, failures, rec)
    except ImportError:
        pass
    return {"decision": decision, "rule": rule, "message": msg}


def decisions(db: Session, student_id: int, course_id: int) -> list[dict]:
    clos = {o.id: o.code for o in db.scalars(select(CLO).where(CLO.course_id == course_id))}
    return [{"id": d.id, "at": d.at, "clo_code": clos.get(d.clo_id), "rule": d.rule, "decision": d.decision,
             "inputs": d.inputs, "message": d.message, "from_path_id": d.from_path_id, "to_path_id": d.to_path_id}
            for d in db.scalars(select(PathDecision).where(PathDecision.student_id == student_id, PathDecision.course_id == course_id)
                                .order_by(PathDecision.id.desc()))]

