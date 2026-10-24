"""Explicit state machine for full Findoc guarded runs."""
from __future__ import annotations


RUN_STATES = {
    "created",
    "running",
    "parsed",
    "evidence_ready",
    "judged",
    "gated",
    "completed",
    "needs_review",
    "degraded",
    "failed",
}
TERMINAL_STATES = {"completed", "needs_review", "degraded", "failed"}
TRANSITIONS = {
    "created": {"running", "failed"},
    "running": {"parsed", "failed"},
    "parsed": {"evidence_ready", "failed"},
    "evidence_ready": {"judged", "failed"},
    "judged": {"gated", "failed"},
    "gated": {"completed", "needs_review", "degraded", "failed"},
    "completed": set(),
    "needs_review": set(),
    "degraded": set(),
    "failed": set(),
}


class InvalidStateTransition(ValueError):
    """Raised when a run attempts an illegal state transition."""


def require_transition(current: str, target: str) -> None:
    if current not in RUN_STATES or target not in TRANSITIONS.get(current, set()):
        raise InvalidStateTransition(f"illegal run state transition: {current} -> {target}")
