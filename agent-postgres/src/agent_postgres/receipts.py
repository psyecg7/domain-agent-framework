"""Transactional broker-event receipts for PostgreSQL-backed consumers.

This is an adapter-level composition aid, not an ``agent_core`` primitive. A
consumer supplies its domain mutation as a callable using the connection it is
given. The event receipt and that mutation commit together, so a redelivered
event cannot apply the co-located database effect twice.

By itself it cannot make an HTTP call, device command, or a raw
``Agent.process()`` call exactly-once: those operations do not automatically
share this transaction. Such effects retain their domain-owned idempotency and
reconciliation contracts. ``PostgresAgentReceiptRunner`` supplies the narrower
state-only Agent composition.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TypeVar

from sqlalchemy import Column, DateTime, MetaData, String, Table, create_engine, insert, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError


T = TypeVar("T")


class EventReceiptStoreUnavailable(RuntimeError):
    """The receipt database could not be reached or its transaction committed."""


@dataclass(frozen=True)
class EventReceiptResult:
    """Outcome of applying one event through a transactional receipt claim."""

    event_id: str
    applied: bool


class PostgresEventReceiptStore:
    """Atomically claim one broker event and run its database mutation once.

    ``apply`` receives the SQLAlchemy connection belonging to the receipt
    transaction. It must perform only database work through that connection.
    If it raises, both its writes and the receipt claim roll back; the broker
    record remains eligible for redelivery because the caller should not commit
    its offset. A duplicate committed receipt skips ``apply``.

    PostgreSQL's primary-key constraint arbitrates concurrent consumers. The
    implementation also supports SQLite for deterministic unit tests, but
    PostgreSQL (or another database with equivalent transaction isolation) is
    required for a multi-process production topology.
    """

    def __init__(self, database_url: str, *, table_name: str = "agent_event_receipts") -> None:
        if not isinstance(table_name, str) or not table_name.isidentifier():
            raise ValueError("table_name must be a SQL identifier")
        try:
            self.engine: Engine = create_engine(database_url)
            metadata = MetaData()
            self.receipts = Table(
                table_name,
                metadata,
                Column("event_id", String(255), primary_key=True),
                Column("applied_at", DateTime(timezone=True), nullable=False),
            )
            metadata.create_all(self.engine)
        except SQLAlchemyError as exc:
            raise EventReceiptStoreUnavailable("Event receipt storage is unavailable") from exc

    def apply_once(self, event_id: str, *, apply: Callable[[Connection], T]) -> EventReceiptResult:
        """Run ``apply`` once for ``event_id`` in the receipt transaction.

        The result deliberately carries no cached handler return value. A
        broker consumer needs to know only whether it performed the mutation;
        any business result belongs in the domain's durable state or outbox.
        """
        self._validate_event_id(event_id)
        if not callable(apply):
            raise TypeError("apply must be callable")
        try:
            with self.engine.begin() as connection:
                connection.execute(insert(self.receipts).values(event_id=event_id, applied_at=datetime.now(timezone.utc)))
                apply(connection)
                return EventReceiptResult(event_id=event_id, applied=True)
        except IntegrityError:
            # Do not translate an unrelated domain constraint into a duplicate
            # delivery. Only a committed receipt proves another worker won.
            if self.has_receipt(event_id):
                return EventReceiptResult(event_id=event_id, applied=False)
            raise
        except SQLAlchemyError as exc:
            raise EventReceiptStoreUnavailable("Event receipt storage is unavailable") from exc

    def has_receipt(self, event_id: str) -> bool:
        """Return whether a prior transactional consumer committed this event."""
        self._validate_event_id(event_id)
        try:
            with self.engine.connect() as connection:
                return connection.execute(
                    select(self.receipts.c.event_id).where(self.receipts.c.event_id == event_id)
                ).first() is not None
        except SQLAlchemyError as exc:
            raise EventReceiptStoreUnavailable("Event receipt storage is unavailable") from exc

    @staticmethod
    def _validate_event_id(event_id: str) -> None:
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("event_id must be a non-empty string")


__all__ = ["EventReceiptResult", "EventReceiptStoreUnavailable", "PostgresEventReceiptStore"]
