"""All ORM models. Importing this package registers every table on Base.metadata."""
from .ai import AICall, ReviewTask  # noqa: F401
from .appeals import Appeal, Notification  # noqa: F401
from .audit import AuditEvent  # noqa: F401
from .audit import install_guards as _audit_guards
from .consent import ConsentRecord  # noqa: F401
from .content import ContentDoc, ContentErrorReport, ContentVersion, StudentLesson  # noqa: F401
from .criteria import AttainmentCriterion  # noqa: F401
from .curriculum import CurriculumVersion, RoadmapEntry  # noqa: F401
from .diagnostic import DiagnosticAttempt, DiagnosticResponse, ReadinessResult  # noqa: F401
from .enrollment import Enrollment  # noqa: F401
from .escalation import Escalation, Intervention  # noqa: F401
from .hints import HintRequest  # noqa: F401
from .items import DiagnosticConfig, Item  # noqa: F401
from .mappings import CloPloMapping  # noqa: F401
from .mastery import MasteryEvidence, MasteryRecord  # noqa: F401
from .offerings import InstructorAssignment, Section, Term  # noqa: F401
from .outcomes import CLO, PLO, Course, Program, ReviewAck, Unit  # noqa: F401
from .paths import LearningPath, PathDecision, PathStep  # noqa: F401
from .quiz import QuizAnswer, QuizAttempt, QuizDebrief  # noqa: F401
from .reteach import ReteachCycle  # noqa: F401
from .sources import Source, SourceChunk  # noqa: F401
from .topics import PracticeSet, StudentNote, Topic  # noqa: F401
from .user import AuthSession, LoginAttempt, OneTimeToken, User  # noqa: F401

_GUARDS: list = [_audit_guards]


def install_db_guards(engine):
    """Database-level protections installed after tables are created."""
    for guard in _GUARDS:
        guard(engine)
