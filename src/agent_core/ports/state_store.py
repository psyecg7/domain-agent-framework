from __future__ import annotations

from typing import Protocol

from agent_core.primitives.state import State


class StateStore(Protocol):
    def get(self, entity_id: str, entity_type: str) -> State | None:
        ...

    def save(self, state: State) -> None:
        ...
