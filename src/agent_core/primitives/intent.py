from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from types import MappingProxyType


@dataclass(frozen=True)
class Intent:
    """A declarative request for an operation, not an executable action."""

    intent_type: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        if not self.intent_type.strip():
            raise ValueError("Intent type must not be empty")
        if self.idempotency_key is not None and not self.idempotency_key.strip():
            raise ValueError("Idempotency key must not be empty")
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


__all__ = ["Intent"]
