"""Mutable current structured facts for one entity.

`Agent.process()` applies observations to State before policy evaluation. Each
update increments its local version. Persistence and cross-worker conditional
writes are responsibilities of the StateStore adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .observation import Observation


@dataclass
class State:
    entity_id: str
    entity_type: str
    values: dict[str, Any] = field(default_factory=dict)
    version: int = 0
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def apply_observation(self, observation: Observation) -> None:
        self.values[observation.name] = observation.value
        self.version += 1
        self.updated_at = datetime.now(timezone.utc)

    def __post_init__(self) -> None:
        if self.updated_at.tzinfo is None:
            self.updated_at = self.updated_at.replace(tzinfo=timezone.utc)
        else:
            self.updated_at = self.updated_at.astimezone(timezone.utc)
