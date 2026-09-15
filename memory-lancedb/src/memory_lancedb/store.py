from __future__ import annotations

from pathlib import Path
from typing import Any

import lancedb

from agent_core import Memory, MemoryResult

from .embeddings import EmbeddingProvider, HashEmbeddingProvider
from .serialization import MemorySerializer


class LanceDBMemoryStore:
    """Persistent semantic memory backed by a local or remote LanceDB table."""

    table_name = "memories"

    def __init__(
        self,
        database_path: str | Path,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        table_name: str = table_name,
    ) -> None:
        self.database_path = str(database_path)
        self._db = lancedb.connect(self.database_path)
        self.embedding_provider = embedding_provider or HashEmbeddingProvider()
        self.table_name = table_name

    def store(self, memory: Memory) -> None:
        record = MemorySerializer.to_record(memory, self.embedding_provider.embed(memory.content))
        if not self._table_exists():
            self._db.create_table(self.table_name, data=[record])
            return

        table = self._db.open_table(self.table_name)
        table.delete(f"memory_id = {_sql_string(memory.memory_id)}")
        table.add([record])

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        entity_id: str | None = None,
        entity_type: str | None = None,
    ) -> list[MemoryResult]:
        if not query.strip():
            raise ValueError("Search query must not be empty")
        if limit <= 0 or not self._table_exists():
            return []

        table = self._db.open_table(self.table_name)
        search = table.search(self.embedding_provider.embed(query)).distance_type("cosine")
        filters = []
        if entity_id is not None:
            filters.append(f"entity_id = {_sql_string(entity_id)}")
        if entity_type is not None:
            filters.append(f"entity_type = {_sql_string(entity_type)}")
        if filters:
            search = search.where(" AND ".join(filters))

        results: list[MemoryResult] = []
        for record in search.limit(limit).to_list():
            distance = float(record.pop("_distance", 0.0))
            results.append(MemoryResult(memory=MemorySerializer.from_record(record), score=1.0 - distance))
        return results

    def _table_exists(self) -> bool:
        return self.table_name in self._db.list_tables().tables


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


__all__ = ["LanceDBMemoryStore"]
