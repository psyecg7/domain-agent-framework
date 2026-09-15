"""Boundary for performing an already-authorized Action.

`Agent` creates Actions from Decisions, then calls this port when an executor
is configured. Implementations own side-effect safety, including any provider
idempotency, transactions, and reconciliation; the core only supplies the
instruction.
"""

from __future__ import annotations

from typing import Protocol

from agent_core.primitives.action import Action


class ActionExecutor(Protocol):
    def execute(self, action: Action) -> None:
        ...
