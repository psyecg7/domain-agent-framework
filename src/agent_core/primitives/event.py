from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid


@dataclass(frozen=True)
class Event:
    event_type: str
    entity_id: str
    entity_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str | None = None
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None:
            object.__setattr__(self, "occurred_at", self.occurred_at.replace(tzinfo=timezone.utc))
        else:
            utc_time = self.occurred_at.astimezone(timezone.utc)
            object.__setattr__(self, "occurred_at", utc_time)
        if self.idempotency_key is not None and not self.idempotency_key.strip():
            raise ValueError("Idempotency key must not be empty")
