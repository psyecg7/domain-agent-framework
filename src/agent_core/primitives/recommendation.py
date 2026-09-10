from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class Recommendation:
    """A non-authoritative proposal produced by a reasoner."""

    recommendation_type: str
    rationale: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.recommendation_type.strip():
            raise ValueError("Recommendation type must not be empty")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Recommendation confidence must be between 0 and 1")


__all__ = ["Recommendation"]
