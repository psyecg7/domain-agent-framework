from __future__ import annotations

from typing import Protocol

from agent_core.primitives.action import Action


class ActionExecutor(Protocol):
    def execute(self, action: Action) -> None:
        ...
