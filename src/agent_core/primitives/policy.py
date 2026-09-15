"""Simple declarative policy data for applications that choose to use it.

The core runtime depends on the PolicyEngine port, not this class. This value
can describe a state predicate and its intended Decision fields; an application
still decides how to evaluate and register it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .state import State


@dataclass(frozen=True)
class Policy:
    name: str
    condition: Callable[[State], bool]
    decision_type: str
    severity: str
    reason: str
