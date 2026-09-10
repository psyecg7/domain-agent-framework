from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid


@dataclass(frozen=True)
class Memory:
    """Technology-neutral information that can be retrieved as context."""

    content: str
    entity_id: str
    entity_type: str
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    memory_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValueError("Memory content must not be empty")
        if self.created_at.tzinfo is None:
            object.__setattr__(self, "created_at", self.created_at.replace(tzinfo=timezone.utc))
        else:
            object.__setattr__(self, "created_at", self.created_at.astimezone(timezone.utc))


__all__ = ["Memory"]
