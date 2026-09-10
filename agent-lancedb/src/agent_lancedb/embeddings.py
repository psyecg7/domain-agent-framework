from __future__ import annotations

from hashlib import sha256
import math
import re
from typing import Protocol


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float]:
        ...


class HashEmbeddingProvider:
    """Deterministic local embeddings for tests and dependency-light deployments."""

    def __init__(self, dimensions: int = 128) -> None:
        if dimensions <= 0:
            raise ValueError("Embedding dimensions must be positive")
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        tokens = re.findall(r"[a-z0-9]+", text.lower())
        vector = [0.0] * self.dimensions
        for token in tokens:
            digest = sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]


__all__ = ["EmbeddingProvider", "HashEmbeddingProvider"]
