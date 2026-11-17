"""US-4.2 / US-6.1 - Assessment item bank (diagnostic, formative and reteach items)."""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..core.crypto import EncryptedString
from ..db import Base, utcnow

PURPOSES = ("diagnostic", "formative", "reteach")
# Question types. Multiple choice everywhere; formative questions may also be true/false, fill in the blank or a short
# written answer. Every type goes through the same teacher approval before a student can see it.
QTYPES = ("mcq", "true_false", "fill_blank", "short_answer")
WRITTEN_QTYPES = ("true_false", "fill_blank", "short_answer")


class Item(Base):
    __tablename__ = "items"
    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), index=True)
    clo_id: Mapped[int] = mapped_column(ForeignKey("clos.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(20), index=True)
    stem: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSON)
    answer_index: Mapped[str] = mapped_column(EncryptedString())  # answer key encrypted at rest
    explanation: Mapped[str] = mapped_column(Text)
    hints: Mapped[list] = mapped_column(JSON, default=list)  # static, progressive hints (fallback for US-6.2)
    qtype: Mapped[str | None] = mapped_column(String(20), nullable=True)  # None / "mcq" = multiple choice
    # written types: the key, encrypted like answer_index - JSON {"answer", "accepted": [...], "key_points": [...]}
    answer_key: Mapped[str | None] = mapped_column(EncryptedString(), nullable=True)
    difficulty: Mapped[int] = mapped_column(Integer, default=2)  # 1 easy, 2 medium, 3 hard
    # pending -> approved | rejected ; approved -> retired (e.g. replaced by a new version)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    origin: Mapped[str] = mapped_column(String(20))  # ai | offline | manual
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"), nullable=True)
    source_chunk_ids: Mapped[list] = mapped_column(JSON, default=list)
    ai_call_id: Mapped[int | None] = mapped_column(ForeignKey("ai_calls.id"), nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    @property
    def answer(self) -> int:
        return int(self.answer_index)

    @property
    def kind(self) -> str:
        return self.qtype or "mcq"

    @property
    def key(self) -> dict:
        """The written answer key (empty for multiple choice)."""
        import json

        try:
            return json.loads(self.answer_key) if self.answer_key else {}
        except ValueError:
            return {}

    @property
    def key_text(self) -> str:
        """The correct answer as text (the tutor and hints must never reveal it)."""
        if self.kind in ("mcq", "true_false"):
            try:
                return self.options[self.answer]
            except (IndexError, TypeError, ValueError):
                return ""
        return str(self.key.get("answer") or "")


class DiagnosticConfig(Base):
    __tablename__ = "diagnostic_configs"
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"), primary_key=True)
    min_items_per_clo: Mapped[int] = mapped_column(Integer, default=3)
    items_per_clo: Mapped[int] = mapped_column(Integer, default=3)  # asked per CLO in a sitting
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
