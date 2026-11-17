"""Development helpers - only mounted when APP_ENV is development/test."""
from fastapi import APIRouter

from ..services.email import list_outbox

router = APIRouter(prefix="/api/dev", tags=["dev"])


@router.get("/outbox")
def outbox(to: str = ""):
    """Demo mailbox: the e-mails the app would have sent to ONE address (verification / reset links), newest first.
    Without an address nothing is listed, so the page never shows everyone who has registered."""
    to = to.strip().lower()
    if not to:
        return []
    return list_outbox(limit=5, to=to)
