"""US-10.1 - Show student progress: CLO attainment, weak areas, next steps, downloadable report (PDF / CSV)."""
import csv
import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.audit import learning, record
from ..core.rbac import ensure_course_access, require
from ..db import get_db, utcnow
from ..models.outcomes import CLO, Course
from ..models.user import User
from ..services import criteria as crit
from ..services import mastery
from ..services import paths as path_svc
from ..services.diagnostic import latest_readiness

router = APIRouter(prefix="/api/progress", tags=["progress"])


def build(db: Session, student: User, course_id: int) -> dict:
    course = db.get(Course, course_id)
    if not course:
        raise HTTPException(404, "Course not found")
    ready = latest_readiness(db, student.id, course_id)
    rows = []
    for clo in db.scalars(select(CLO).where(CLO.course_id == course_id).order_by(CLO.code)):
        rec = mastery.current(db, student.id, clo.id)
        thr = rec.threshold if rec else crit.resolve(db, clo)["threshold"]
        r = ready.get(clo.id)
        rows.append({"clo_id": clo.id, "clo_code": clo.code, "description": clo.description, "threshold": thr,
                     "mastery_pct": rec.mastery_pct if rec else None, "attained": bool(rec and rec.attained),
                     "status": "attained" if rec and rec.attained else "below" if rec else "not_assessed",
                     "attempts": rec.attempts_counted if rec else 0, "decided_by": rec.trigger if rec else None,
                     "readiness": r.level if r else None, "readiness_pct": r.score_pct if r else None,
                     "gap": round(thr - rec.mastery_pct, 1) if rec and not rec.attained else None})
    weak = sorted([r for r in rows if r["status"] == "below"], key=lambda r: -r["gap"]) + \
        [r for r in rows if r["status"] == "not_assessed"]
    p = path_svc.active_path(db, student.id, course_id)
    steps = path_svc.steps(db, p) if p else []
    codes = {r["clo_id"]: r["clo_code"] for r in rows}
    nxt = [{"step_id": s.id, "clo_id": s.clo_id, "clo_code": codes.get(s.clo_id), "unit_id": s.unit_id, "step_type": s.step_type,
            "label": path_svc.STEP_LABEL.get(s.step_type, s.step_type), "rationale": s.rationale}
           for s in steps if s.status == "pending"][:3]
    attained = sum(1 for r in rows if r["attained"])
    advice = []
    for w in weak[:3]:
        advice.append(f"{w['clo_code']}: " + (f"{w['mastery_pct']:.0f}% - {w['gap']:.0f} points below the {w['threshold']:.0f}% target. Practise it again or ask for a hint."
                                              if w["status"] == "below" else "not assessed yet - take its formative quiz."))
    return {
        "student": {"id": student.id, "name": student.full_name, "student_number": student.student_number},
        "course": {"id": course.id, "code": course.code, "title": course.title},
        "generated_at": utcnow(),
        "summary": {"attained": attained, "total": len(rows), "attained_pct": round(100.0 * attained / len(rows), 1) if rows else 0,
                    "path_done": sum(1 for s in steps if s.status in ("done", "skipped")), "path_total": len(steps)},
        "clos": rows, "weak_areas": [{"clo_code": w["clo_code"], "description": w["description"], "status": w["status"],
                                      "mastery_pct": w["mastery_pct"], "gap": w["gap"]} for w in weak],
        "next_steps": nxt, "advice": advice,
        "policy": mastery.POLICY["summary"],
    }


def to_csv(d: dict) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Student", d["student"]["name"], d["student"]["student_number"] or ""])
    w.writerow(["Course", f"{d['course']['code']} {d['course']['title']}"])
    w.writerow(["Generated (UTC)", d["generated_at"].strftime("%Y-%m-%d %H:%M")])
    w.writerow([])
    w.writerow(["CLO", "Description", "Status", "Mastery %", "Target %", "Attempts counted", "Diagnostic readiness"])
    for r in d["clos"]:
        w.writerow([r["clo_code"], r["description"], r["status"], "" if r["mastery_pct"] is None else r["mastery_pct"],
                    r["threshold"], r["attempts"], r["readiness"] or ""])
    w.writerow([])
    w.writerow(["Next steps"])
    for s in d["next_steps"]:
        w.writerow([s["clo_code"], s["label"], s["rationale"]])
    return buf.getvalue().encode("utf-8-sig")


def to_pdf(d: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    st = getSampleStyleSheet()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=f"Progress report {d['course']['code']}")
    s = d["summary"]
    story = [Paragraph("AI Tutor - Progress report", st["Title"]),
             Paragraph(f"{d['student']['name']} ({d['student']['student_number'] or '-'}) &middot; {d['course']['code']} {d['course']['title']}", st["Normal"]),
             Paragraph(f"Generated {d['generated_at']:%Y-%m-%d %H:%M} UTC", st["Normal"]), Spacer(1, 10),
             Paragraph(f"<b>{s['attained']} of {s['total']}</b> course learning outcomes attained ({s['attained_pct']}%). "
                       f"Learning path: {s['path_done']} of {s['path_total']} steps complete.", st["Normal"]), Spacer(1, 10)]
    data = [["CLO", "Status", "Mastery", "Target", "Attempts"]]
    for r in d["clos"]:
        data.append([r["clo_code"], r["status"].replace("_", " "), "-" if r["mastery_pct"] is None else f"{r['mastery_pct']:.0f}%",
                     f"{r['threshold']:.0f}%", str(r["attempts"])])
    t = Table(data, hAlign="LEFT")
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2d5bd7")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("GRID", (0, 0), (-1, -1), 0.5, colors.grey)]))
    story += [t, Spacer(1, 12), Paragraph("Weak areas and advice", st["Heading2"])]
    story += [Paragraph(a, st["Normal"]) for a in d["advice"]] or [Paragraph("No weak areas - well done!", st["Normal"])]
    story += [Spacer(1, 8), Paragraph("Next steps", st["Heading2"])]
    story += [Paragraph(f"{x['clo_code']} - {x['label']}: {x['rationale']}", st["Normal"]) for x in d["next_steps"]] or \
        [Paragraph("Your learning path is complete.", st["Normal"])]
    story += [Spacer(1, 8), Paragraph(f"How mastery is calculated: {d['policy']}", st["Italic"])]
    doc.build(story)
    return buf.getvalue()


def _download(d: dict, fmt: str) -> Response:
    name = f"progress-{d['course']['code']}-{d['student']['student_number'] or d['student']['id']}"
    if fmt == "csv":
        return Response(to_csv(d), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
    if fmt == "pdf":
        return Response(to_pdf(d), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})
    raise HTTPException(422, "format must be pdf or csv")


@router.get("/me/{course_id}")
def my_progress(course_id: int, db: Session = Depends(get_db), user: User = Depends(require("progress:self"))):
    ensure_course_access(db, user, course_id)
    return build(db, user, course_id)


@router.get("/me/{course_id}/download")
def my_download(course_id: int, format: str = "pdf", db: Session = Depends(get_db), user: User = Depends(require("progress:self"))):
    ensure_course_access(db, user, course_id)
    d = build(db, user, course_id)
    learning(db, user, "progress_downloaded", "course", course_id, {"format": format}, course_id=course_id)
    db.commit()
    return _download(d, format)


@router.get("/course/{course_id}/student/{student_id}")
def staff_progress(course_id: int, student_id: int, db: Session = Depends(get_db), user: User = Depends(require("reports:read"))):
    ensure_course_access(db, user, course_id)
    st = db.get(User, student_id)
    if not st:
        raise HTTPException(404, "Student not found")
    return build(db, st, course_id)


@router.get("/course/{course_id}/student/{student_id}/download")
def staff_download(course_id: int, student_id: int, format: str = "pdf", db: Session = Depends(get_db),
                   user: User = Depends(require("reports:read"))):
    ensure_course_access(db, user, course_id)
    st = db.get(User, student_id)
    if not st:
        raise HTTPException(404, "Student not found")
    record(db, user, "progress.downloaded", "user", student_id, {"format": format}, course_id=course_id)
    db.commit()
    return _download(build(db, st, course_id), format)
