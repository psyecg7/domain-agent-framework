"""A lightweight identity and attributes value for a domain entity.

Entity is descriptive only. Mutable current facts live in State, and an adapter
owns persistence when an application needs it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Entity:
    entity_id: str
    entity_type: str
    attributes: dict[str, Any] = field(default_factory=dict)
