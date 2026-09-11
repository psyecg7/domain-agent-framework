from __future__ import annotations

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, insert, select

from agent_postgres import PostgresEventReceiptStore


def _effects_table(store: PostgresEventReceiptStore) -> Table:
    metadata = MetaData()
    effects = Table(
        "receipt_effects", metadata,
        Column("effect_id", String(255), primary_key=True),
        Column("count", Integer, nullable=False),
    )
    metadata.create_all(store.engine)
    return effects


def test_duplicate_event_skips_a_co_located_database_effect(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'receipts.db'}"
    first = PostgresEventReceiptStore(database_url)
    second = PostgresEventReceiptStore(database_url)
    effects = _effects_table(first)

    def apply(connection) -> None:
        connection.execute(insert(effects).values(effect_id="reservation-1", count=1))

    assert first.apply_once("event-1", apply=apply).applied is True
    assert second.apply_once("event-1", apply=apply).applied is False
    with first.engine.connect() as connection:
        assert connection.execute(select(effects.c.count)).scalar_one() == 1


def test_failed_mutation_rolls_back_its_receipt_and_is_redeliverable(tmp_path) -> None:
    store = PostgresEventReceiptStore(f"sqlite:///{tmp_path / 'receipts.db'}")
    effects = _effects_table(store)

    def fail(connection) -> None:
        connection.execute(insert(effects).values(effect_id="reservation-1", count=1))
        raise RuntimeError("simulated handler crash")

    with pytest.raises(RuntimeError, match="simulated handler crash"):
        store.apply_once("event-1", apply=fail)
    assert store.has_receipt("event-1") is False
    with store.engine.connect() as connection:
        assert connection.execute(select(effects.c.count)).first() is None

    assert store.apply_once(
        "event-1", apply=lambda connection: connection.execute(insert(effects).values(effect_id="reservation-1", count=1)),
    ).applied is True
    assert store.has_receipt("event-1") is True


def test_event_receipt_rejects_invalid_event_identity(tmp_path) -> None:
    store = PostgresEventReceiptStore(f"sqlite:///{tmp_path / 'receipts.db'}")
    with pytest.raises(ValueError, match="event_id"):
        store.apply_once("", apply=lambda _: None)
