"""Dependency-free, payload-free dispatcher lifecycle records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal


DispatchPhase = Literal["succeeded", "failed"]


@dataclass(frozen=True)
class RedpandaDispatchEvent:
    """One completed dispatcher poll, without broker record payloads."""

    phase: DispatchPhase
    occurred_at: datetime
    duration_ms: float
    polled_records: int
    delivered_handlers: int
    acknowledged_records: int
    terminal_records: int
    error_type: str | None = None


def now() -> datetime:
    return datetime.now(timezone.utc)


__all__ = ["RedpandaDispatchEvent"]
