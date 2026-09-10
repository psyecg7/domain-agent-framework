from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid


@dataclass(frozen=True)
class Decision:
    entity_id: str
    entity_type: str
    decision_type: str
    severity: str
    reason: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    confidence: float | None = None
    decision_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None:
            object.__setattr__(self, "created_at", self.created_at.replace(tzinfo=timezone.utc))
        else:
            utc_time = self.created_at.astimezone(timezone.utc)
            object.__setattr__(self, "created_at", utc_time)
