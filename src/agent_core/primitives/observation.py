"""One normalized fact observed from an Event.

The runtime decomposes an event payload into observations, then applies each
observation to the target State. This makes the state update step explicit
without giving observations business-policy authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid


@dataclass(frozen=True)
class Observation:
    entity_id: str
    entity_type: str
    name: str
    value: Any
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    observation_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            object.__setattr__(self, "observed_at", self.observed_at.replace(tzinfo=timezone.utc))
        else:
            utc_time = self.observed_at.astimezone(timezone.utc)
            object.__setattr__(self, "observed_at", utc_time)
