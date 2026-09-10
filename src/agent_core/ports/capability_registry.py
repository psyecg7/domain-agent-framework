from __future__ import annotations

from typing import Protocol, Sequence

from agent_core.primitives.capability import Capability
from agent_core.primitives.intent import Intent


class CapabilityRegistry(Protocol):
    def register(self, capability: Capability) -> None:
        ...

    def get(self, capability_id: str) -> Capability | None:
        ...

    def list(self) -> Sequence[Capability]:
        ...

    def find(self, intent: Intent) -> Sequence[Capability]:
        ...


class InMemoryCapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: dict[str, Capability] = {}

    def register(self, capability: Capability) -> None:
        self._capabilities[capability.capability_id] = capability

    def get(self, capability_id: str) -> Capability | None:
        return self._capabilities.get(capability_id)

    def list(self) -> Sequence[Capability]:
        return tuple(self._capabilities.values())

    def find(self, intent: Intent) -> Sequence[Capability]:
        return tuple(capability for capability in self._capabilities.values() if capability.supports(intent))


__all__ = ["CapabilityRegistry", "InMemoryCapabilityRegistry"]
