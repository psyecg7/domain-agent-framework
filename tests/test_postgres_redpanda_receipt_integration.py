"""Opt-in proof of Redpanda redelivery composed with PostgreSQL receipts."""

from __future__ import annotations

import os
import time
import uuid

import pytest
from sqlalchemy import Column, Integer, MetaData, String, Table, insert, select


DATABASE_URL = os.getenv("POSTGRES_ATOMIC_DATABASE_URL")
BOOTSTRAP = os.getenv("REDPANDA_BOOTSTRAP_SERVERS")
EXAMPLE_DATABASE_URL = "postgresql+psycopg://user:password@host/db"
pytestmark = pytest.mark.integration

if not DATABASE_URL or DATABASE_URL == EXAMPLE_DATABASE_URL:
    pytest.skip("set POSTGRES_ATOMIC_DATABASE_URL to run PostgreSQL receipt integration", allow_module_level=True)
if not BOOTSTRAP:
    pytest.skip("set REDPANDA_BOOTSTRAP_SERVERS to run Redpanda receipt integration", allow_module_level=True)

pytest.importorskip("confluent_kafka")

from agent_core import Agent, Event
from agent_postgres import (
    PostgresAgentReceiptRunner,
    PostgresEventReceiptStore,
    PostgresStateStore,
)
from agent_redpanda import RedpandaConsumer, RedpandaEventDispatcher, RedpandaEventTransport, RedpandaProducer


def _consumer(topic: str, group_id: str) -> RedpandaConsumer:
    return RedpandaConsumer(
        {"bootstrap.servers": BOOTSTRAP, "group.id": group_id, "auto.offset.reset": "earliest"},
        topics=[topic],
    )


def _dispatch_until(dispatcher: RedpandaEventDispatcher, predicate) -> None:
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        dispatcher.dispatch(timeout=1.0)
        if predicate():
            return
    pytest.fail("broker did not deliver the expected receipt event")


def test_redelivery_after_a_crash_skips_a_previously_committed_postgres_effect() -> None:
    """The receipt survives consumer restart before the source offset commits."""
    prefix = f"receipt_{uuid.uuid4().hex[:16]}"
    topic, group_id = f"{prefix}-events", f"{prefix}-group"
    receipts = PostgresEventReceiptStore(DATABASE_URL, table_name=f"{prefix}_receipts")
    metadata = MetaData()
    effects = Table(
        f"{prefix}_effects", metadata,
        Column("event_id", String(255), primary_key=True),
        Column("count", Integer, nullable=False),
    )
    metadata.create_all(receipts.engine)
    event = Event("order.received", "ORD-RECEIPT-1", "order", {"quantity": 1}, source="orders")
    transport = RedpandaEventTransport(
        RedpandaProducer({"bootstrap.servers": BOOTSTRAP}), topic_for_event=lambda _: topic,
    )
    transport.publish(event)

    first_consumer = _consumer(topic, group_id)
    first_dispatcher = RedpandaEventDispatcher(first_consumer)

    def apply_then_crash(delivered: Event) -> None:
        result = receipts.apply_once(
            delivered.event_id,
            apply=lambda connection: connection.execute(
                insert(effects).values(event_id=delivered.event_id, count=1)
            ),
        )
        assert result.applied is True
        raise RuntimeError("simulated crash after database commit before offset acknowledgement")

    first_dispatcher.subscribe(event.event_type, apply_then_crash)
    try:
        with pytest.raises(RuntimeError, match="simulated crash"):
            _dispatch_until(first_dispatcher, lambda: False)
    finally:
        first_consumer.close()

    restarted_receipts = PostgresEventReceiptStore(DATABASE_URL, table_name=f"{prefix}_receipts")
    restarted_consumer = _consumer(topic, group_id)
    restarted_dispatcher = RedpandaEventDispatcher(restarted_consumer)
    replay_results = []

    def apply_after_restart(delivered: Event) -> None:
        replay_results.append(restarted_receipts.apply_once(
            delivered.event_id,
            apply=lambda connection: connection.execute(
                insert(effects).values(event_id=delivered.event_id, count=1)
            ),
        ))

    restarted_dispatcher.subscribe(event.event_type, apply_after_restart)
    try:
        _dispatch_until(restarted_dispatcher, lambda: len(replay_results) == 1)
        with receipts.engine.connect() as connection:
            effect_count = connection.execute(select(effects.c.count)).scalar_one()
        receipt_committed = receipts.has_receipt(event.event_id)
    finally:
        restarted_consumer.close()
        metadata.drop_all(receipts.engine)
        receipts.receipts.drop(receipts.engine)

    assert replay_results[0].applied is False
    assert effect_count == 1
    assert receipt_committed is True


def test_redelivery_after_a_crash_skips_a_previously_committed_agent_observation() -> None:
    """The state-only Agent runner shares the receipt transaction with state."""
    class NoDecisions:
        def evaluate(self, state):
            return []

    prefix = f"agent_receipt_{uuid.uuid4().hex[:16]}"
    topic, group_id = f"{prefix}-events", f"{prefix}-group"
    state_store = PostgresStateStore(DATABASE_URL, table_name=f"{prefix}_states")
    receipts = PostgresEventReceiptStore(DATABASE_URL, table_name=f"{prefix}_receipts")
    runner = PostgresAgentReceiptRunner(
        Agent(state_store, NoDecisions()), state_store=state_store, receipt_store=receipts,
    )
    event = Event("order.received", "ORD-AGENT-RECEIPT-1", "order", {"quantity": 1}, source="orders")
    RedpandaEventTransport(
        RedpandaProducer({"bootstrap.servers": BOOTSTRAP}), topic_for_event=lambda _: topic,
    ).publish(event)

    first_consumer = _consumer(topic, group_id)
    first_dispatcher = RedpandaEventDispatcher(first_consumer)

    def observe_then_crash(delivered: Event) -> None:
        assert runner.process_once(delivered).receipt.applied is True
        raise RuntimeError("simulated crash after Agent state commit before offset acknowledgement")

    first_dispatcher.subscribe(event.event_type, observe_then_crash)
    try:
        with pytest.raises(RuntimeError, match="simulated crash"):
            _dispatch_until(first_dispatcher, lambda: False)
    finally:
        first_consumer.close()

    restarted_state_store = PostgresStateStore(DATABASE_URL, table_name=f"{prefix}_states")
    restarted_receipts = PostgresEventReceiptStore(DATABASE_URL, table_name=f"{prefix}_receipts")
    restarted_runner = PostgresAgentReceiptRunner(
        Agent(restarted_state_store, NoDecisions()),
        state_store=restarted_state_store,
        receipt_store=restarted_receipts,
    )
    restarted_consumer = _consumer(topic, group_id)
    restarted_dispatcher = RedpandaEventDispatcher(restarted_consumer)
    replay_results = []
    restarted_dispatcher.subscribe(event.event_type, lambda delivered: replay_results.append(restarted_runner.process_once(delivered)))
    try:
        _dispatch_until(restarted_dispatcher, lambda: len(replay_results) == 1)
        stored = restarted_state_store.get(event.entity_id, event.entity_type)
    finally:
        restarted_consumer.close()
        restarted_state_store.table.drop(restarted_state_store.engine)
        restarted_receipts.receipts.drop(restarted_receipts.engine)

    assert replay_results[0].receipt.applied is False
    assert replay_results[0].result is None
    assert stored is not None
    assert stored.values == {"quantity": 1}
    assert stored.version == 1
