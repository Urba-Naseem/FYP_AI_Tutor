"""Pydantic schemas for every structured AI reply (optimisation 3).

Each schema is sent to Gemini as the required reply shape (structured output) AND re-checked in Python
(`ai_gateway.call_json(..., schema=...)`), so code never receives a broken or wrongly shaped reply.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---- assessment items -------------------------------------------------------------------------------


class ItemOut(BaseModel):
    stem: str = Field(description="The question. Markdown allowed (code in backticks, maths in $...$).")
    options: list[str] = Field(min_length=4, max_length=4, description="Exactly 4 distinct answer options.")
    answer_index: int = Field(ge=0, le=3, description="Index (0-3) of the single correct option.")
    explanation: str = Field(description="Why the correct option is right.")
    hints: list[str] = Field(default_factory=list, max_length=3, description="Socratic hints that never reveal the answer.")
    difficulty: int = Field(ge=1, le=3, description="1 easy, 2 medium, 3 hard.")


class ItemsOut(BaseModel):
    items: list[ItemOut]


from .quiz_written import WrittenItemOut  # noqa: E402


class ItemBundleOut(BaseModel):
    """All question types for one CLO in a single call (optimisation 1: fewer calls)."""
    diagnostic: list[ItemOut] = Field(default_factory=list)
    formative: list[ItemOut] = Field(default_factory=list)
    reteach: list[ItemOut] = Field(default_factory=list)
    written: list["WrittenItemOut"] = Field(default_factory=list)  # formative true/false, fill in the blank, short answer


# ---- CLO-PLO mappings --------------------------------------------------------------------------------


class MappingOut(BaseModel):
    clo: str
    plo: str
    strength: int = Field(ge=1, le=3)
    rationale: str


class MappingsOut(BaseModel):
    mappings: list[MappingOut]


# ---- curriculum extraction -------------------------------------------------------------------------------


class ProgramOut(BaseModel):
    code: str | None = Field(default=None, description="Programme code, e.g. BSCS.")
    name: str | None = Field(default=None, description="Programme name.")


class PloOut(BaseModel):
    code: str = Field(description="PLO code, e.g. PLO1.")
    description: str = Field(description="The PLO statement, copied word for word.")


class HeadOut(BaseModel):
    program: ProgramOut
    plos: list[PloOut]


class CloOut(BaseModel):
    code: str = Field(description="CLO1, CLO2, ... (per course).")
    description: str = Field(description="The CLO statement, copied word for word.")
    unit: int | None = Field(default=None, description="Unit number the CLO belongs to, if stated.")
    bloom: str | None = Field(default=None, description="Bloom / taxonomy level as written, e.g. 'L3 - Applying'.")
    weight: float | None = Field(default=None, description="Weight in percent if stated (30% -> 30).")
    plos: list[str] = Field(default_factory=list, description="PLO codes this CLO maps to, if stated.")


class UnitOut(BaseModel):
    number: int
    title: str
    clos: list[str] = Field(default_factory=list)


class CourseOut(BaseModel):
    code: str = Field(description="Course code exactly as written, e.g. CS3683.")
    title: str
    credits: int | None = Field(default=None, description="Total credit hours (4 (3+1) -> 4).")
    semester: int | None = Field(default=None, description="Semester in the programme roadmap, if stated.")
    prerequisites: list[str] = Field(default_factory=list, description="Prerequisite course codes.")
    description: str | None = None
    units: list[UnitOut] = Field(default_factory=list)
    clos: list[CloOut]


class CoursesOut(BaseModel):
    courses: list[CourseOut]


class CurriculumOut(BaseModel):
    program: ProgramOut
    plos: list[PloOut]
    courses: list[CourseOut]


# ---- learning content ----------------------------------------------------------------------------------


class Slide(BaseModel):
    title: str = Field(description="Slide title.")
    points: list[str] = Field(description="2-5 short key points shown on the slide.")
    visual: str = Field(default="", description="Optional Markdown visual: a ```mermaid diagram, a code block, a table or $$maths$$.")
    explanation: str = Field(description="The full spoken explanation of this slide (80-160 words), in Markdown.")


class LessonOut(BaseModel):
    lecture_md: str = Field(description="The full personal lecture in Markdown.")
    notes_md: str = Field(description="5-8 bullet-point revision notes in Markdown.")
    slides: list[Slide] = Field(default_factory=list, description="8-15 slides that present the same lecture.")


# ---- roadmap -----------------------------------------------------------------------------------------------


class RoadmapRowOut(BaseModel):
    semester: int = Field(ge=1, le=20)
    name: str
    credits: str | None = Field(default=None, description="Credit split as written, e.g. '3+1' or '3'.")


class RoadmapRowsOut(BaseModel):
    courses: list[RoadmapRowOut]


class PairOut(BaseModel):
    roadmap: str = Field(description="The study-plan course name, exactly as given.")
    code: str = Field(description="The curriculum course code it is.")


class PairsOut(BaseModel):
    pairs: list[PairOut]


BloomLevel = Literal["remember", "understand", "apply", "analyze", "evaluate", "create"]


# ---- course topics and practice ------------------------------------------------------------------------------

class TopicDraft(BaseModel):
    title: str = Field(description="Short topic title, like a week in a university syllabus.")
    summary: str = Field(description="2-3 sentences: what this topic teaches and the key ideas.")
    clos: list[str] = Field(description="Codes of the CLOs this topic serves, main one first (e.g. ['CLO2']).")


class TopicPlanOut(BaseModel):
    topics: list[TopicDraft]


class PracticeQuestion(BaseModel):
    stem: str = Field(description="The question.")
    options: list[str] = Field(description="Exactly 4 answer options.")
    answer_index: int = Field(description="Index (0-3) of the correct option.")
    explanation: str = Field(description="Why the correct option is right and the others are wrong.")
    hints: list[str] = Field(default_factory=list, description="2 Socratic hints that never reveal the answer.")


class PracticeOut(BaseModel):
    questions: list[PracticeQuestion]
