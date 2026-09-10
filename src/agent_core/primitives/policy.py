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
