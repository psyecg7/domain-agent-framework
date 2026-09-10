from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from types import MappingProxyType

from .intent import Intent


@dataclass(frozen=True)
class Capability:
    """A declarative, non-executable description of a supported intent."""

    capability_id: str
    name: str
    description: str
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    output_schema: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.capability_id.strip():
            raise ValueError("Capability ID must not be empty")
        if not self.name.strip():
            raise ValueError("Capability name must not be empty")
        if not self.description.strip():
            raise ValueError("Capability description must not be empty")
        object.__setattr__(self, "input_schema", MappingProxyType(dict(self.input_schema)))
        object.__setattr__(self, "output_schema", MappingProxyType(dict(self.output_schema)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def supports(self, intent: Intent) -> bool:
        """Match an intent type without validating, authorizing, or executing it."""
        supported_types = self.metadata.get("intent_types", ())
        return self.capability_id == intent.intent_type or intent.intent_type in supported_types


__all__ = ["Capability"]
