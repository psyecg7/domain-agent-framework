from __future__ import annotations

from datetime import datetime, timezone

from agent_core import Memory
from agent_lancedb import HashEmbeddingProvider, LanceDBMemoryStore


def memory(
    memory_id: str,
    content: str,
    *,
    entity_id: str = "entity-1",
    entity_type: str = "entity",
) -> Memory:
    return Memory(
        memory_id=memory_id,
        entity_id=entity_id,
        entity_type=entity_type,
        content=content,
        metadata={"kind": "note"},
        source="test",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


def test_store_and_retrieve(tmp_path) -> None:
    store = LanceDBMemoryStore(tmp_path, embedding_provider=HashEmbeddingProvider(256))
    store.store(memory("m-1", "Supplier delivery delay affected the shipment."))

    results = store.search("supplier delivery delay")

    assert len(results) == 1
    assert results[0].memory.content == "Supplier delivery delay affected the shipment."
    assert results[0].memory.metadata == {"kind": "note"}
    assert results[0].score > 0.5


def test_semantic_similarity_ranks_related_memory_first(tmp_path) -> None:
    store = LanceDBMemoryStore(tmp_path, embedding_provider=HashEmbeddingProvider(256))
    store.store(memory("related", "Supplier delivery delay affected replenishment."))
    store.store(memory("unrelated", "The annual security training is complete."))

    results = store.search("supplier delivery delay")

    assert [result.memory.memory_id for result in results] == ["related", "unrelated"]


def test_metadata_filters_restrict_results(tmp_path) -> None:
    store = LanceDBMemoryStore(tmp_path, embedding_provider=HashEmbeddingProvider(256))
    store.store(memory("product", "Supplier delivery delay for this item.", entity_id="p-1", entity_type="product"))
    store.store(memory("order", "Supplier delivery delay for this order.", entity_id="o-1", entity_type="order"))

    results = store.search("supplier delivery delay", entity_type="product", entity_id="p-1")

    assert [result.memory.memory_id for result in results] == ["product"]


def test_same_memory_id_is_upserted(tmp_path) -> None:
    store = LanceDBMemoryStore(tmp_path, embedding_provider=HashEmbeddingProvider(256))
    store.store(memory("m-1", "The old supplier note."))
    store.store(memory("m-1", "The updated supplier note."))

    results = store.search("updated supplier note")

    assert len(results) == 1
    assert results[0].memory.content == "The updated supplier note."


def test_memory_persists_across_store_restart(tmp_path) -> None:
    first = LanceDBMemoryStore(tmp_path, embedding_provider=HashEmbeddingProvider(256))
    first.store(memory("m-1", "Supplier lead time is three days."))

    second = LanceDBMemoryStore(tmp_path, embedding_provider=HashEmbeddingProvider(256))
    results = second.search("supplier lead time")

    assert results[0].memory.memory_id == "m-1"
    assert results[0].memory.source == "test"
