"""Best-effort audit trail. Recording must never break the request it describes."""
import logging
from flask import current_app, request, session

log = logging.getLogger(__name__)

KEEP_ROWS = 20_000      # oldest rows are pruned beyond this
_MAX = {"target": 200, "detail": 300}


def _client_ip():
    xff = request.headers.get("X-Forwarded-For", "")
    return (xff.split(",")[0].strip() if xff else request.remote_addr) or None


def audit(action, target=None, detail=None, user_id=None, username=None):
    """Record who did what.

    Never put secrets in `detail`: no passwords, tokens, recovery codes or source URLs
    (private calendar links often embed an access key). Names and ids are fine.
    """
    try:
        if user_id is None and session.get("user_id"):
            user_id = session["user_id"]
            username = username or session.get("user_slug")
        current_app.db.add_audit(
            user_id, username, action,
            (target or None) and str(target)[:_MAX["target"]],
            (detail or None) and str(detail)[:_MAX["detail"]],
            _client_ip(), KEEP_ROWS,
        )
    except Exception:
        log.exception("Failed to write audit entry for %s", action)
