"""US-6.1 / US-6.2 / US-6.3 - Formative and reteach quiz attempts."""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base, utcnow


class QuizAttempt(Base):
    __tablename__ = "quiz_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), index=True)
    clo_id: Mapped[int] = mapped_column(ForeignKey("clos.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="formative")  # formative | reteach
    step_id: Mapped[int | None] = mapped_column(ForeignKey("path_steps.id"), nullable=True)
    item_ids: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="in_progress", index=True)  # in_progress | completed
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    score_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_id: Mapped[int | None] = mapped_column(ForeignKey("mastery_evidence.id"), nullable=True)
    mastery_record_id: Mapped[int | None] = mapped_column(ForeignKey("mastery_records.id"), nullable=True)


class QuizAnswer(Base):
    __tablename__ = "quiz_answers"
    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("quiz_attempts.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), index=True)
    item_version: Mapped[int] = mapped_column(Integer)
    chosen: Mapped[int] = mapped_column(Integer)  # option index (true/false: 0 True, 1 False); -1 for a typed answer
    correct: Mapped[bool] = mapped_column(Boolean)
    hints_used: Mapped[int] = mapped_column(Integer, default=0)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # fill in the blank / short answer
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)       # the marker's note on a short answer
    answered_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class QuizDebrief(Base):
    """After a quiz: the AI teacher talks through each wrong answer with the student (why they chose it, what they
    were thinking), re-explains the idea, and records what it learned for reteaching."""
    __tablename__ = "quiz_debriefs"
    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("quiz_attempts.id", ondelete="CASCADE"), unique=True, index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), index=True)
    clo_id: Mapped[int] = mapped_column(ForeignKey("clos.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | done
    wrong: Mapped[list] = mapped_column(JSON, default=list)        # question ids (bank item id, or -n for written) to discuss
    position: Mapped[int] = mapped_column(Integer, default=0)      # index into `wrong`
    turns: Mapped[int] = mapped_column(Integer, default=0)         # student replies on the current question
    messages: Mapped[list] = mapped_column(JSON, default=list)     # [{role: teacher|student, text, q}]
    findings: Mapped[list] = mapped_column(JSON, default=list)     # [{q, n, diagnosis, note}]
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
