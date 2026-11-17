"""Outgoing e-mail. Without SMTP configured, messages are written to the outbox
folder (backend/outbox) so verification / reset links can be demonstrated."""
import json
import re
from datetime import datetime

from ..config import get_settings


def send_email(to: str, subject: str, body: str) -> None:
    folder = get_settings().path(get_settings().outbox_dir)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", to)[:40]
    (folder / f"{stamp}-{safe}.json").write_text(
        json.dumps({"to": to, "subject": subject, "body": body, "sent_at": datetime.now().isoformat()}, indent=2)
    )


def list_outbox(limit: int = 30, to: str | None = None) -> list[dict]:
    """Newest first; `to` keeps only the messages sent to that address (case-insensitive)."""
    folder = get_settings().path(get_settings().outbox_dir)
    if not folder.exists():
        return []
    out = []
    for f in sorted(folder.glob("*.json"), reverse=True):
        m = json.loads(f.read_text())
        if to and str(m.get("to", "")).lower() != to.lower():
            continue
        out.append(m)
        if len(out) >= limit:
            break
    return out
