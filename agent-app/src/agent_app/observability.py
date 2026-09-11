"""Dependency-free lifecycle observation for the local application surface."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal


LifecyclePhase = Literal["started", "succeeded", "failed"]


@dataclass(frozen=True)
class AgentLifecycleEvent:
    """A payload-free processing lifecycle record suitable for logs or metrics."""

    phase: LifecyclePhase
    occurred_at: datetime
    event_id: str
    event_type: str
    entity_id: str
    entity_type: str
    duration_ms: float | None = None
    decision_count: int | None = None
    action_count: int | None = None
    error_type: str | None = None


@dataclass(frozen=True)
class AgentHealth:
    """Local process health and monotonic processing counters."""

    status: Literal["ok", "degraded"]
    processed_events: int
    succeeded_events: int
    failed_events: int
    registered_policies: int
    registered_actions: int


def now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = ["AgentHealth", "AgentLifecycleEvent", "LifecyclePhase"]
