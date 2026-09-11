"""PostgreSQL transaction support for durable operation handling.

This module is deliberately an adapter, not an ``agent_core`` primitive.  A
domain supplies its own mutation using the transaction connection it receives;
this store atomically records the operation outcome and an optional outbound
event alongside that mutation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text, and_, create_engine, insert, or_, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, OperationalError

from agent_core import Event


class AtomicStoreUnavailable(RuntimeError):
    """The transactional durability store could not be reached or committed."""


class OperationIdentityMismatch(RuntimeError):
    """One operation ID was reused with a different outbound event identity."""


@dataclass(frozen=True)
class AtomicOperationResult:
    """The durable result of one domain-owned operation.

    ``duplicate`` means a prior committed attempt already owns this operation
    ID; in that case the supplied mutation was not run again.
    """

    operation_id: str
    outcome: str
    duplicate: bool
    outbox_event_id: str | None


class PostgresAtomicOperationStore:
    """Atomically deduplicate an operation, mutate domain data, and record an outbox event.

    The ``apply`` callback must make only database changes through the supplied
    SQLAlchemy connection.  It must not call a broker, external API, or device:
    those effects cannot share this transaction.  A separate publisher reads
    ``pending_outbox`` and marks a message published *after* broker acceptance.
    Duplicate publication remains possible and must be tolerated by consumers
    using the stable event ID or a domain operation ID.
    """

    def __init__(self, database_url: str, *, table_prefix: str = "agent_atomic") -> None:
        if not isinstance(table_prefix, str) or not table_prefix.isidentifier():
            raise ValueError("table_prefix must be a SQL identifier")
        try:
            self.engine: Engine = create_engine(database_url)
            metadata = MetaData()
            self.operations = Table(
                f"{table_prefix}_operations",
                metadata,
                Column("operation_id", String(255), primary_key=True),
                Column("outcome", String(255), nullable=True),
                Column("outbox_event_id", String(255), nullable=True),
                Column("completed_at", DateTime(timezone=True), nullable=True),
            )
            self.outbox = Table(
                f"{table_prefix}_outbox",
                metadata,
                Column("event_id", String(255), primary_key=True),
                Column("operation_id", String(255), nullable=False, unique=True),
                Column("event_type", String(255), nullable=False),
                Column("entity_id", String(255), nullable=False),
                Column("entity_type", String(255), nullable=False),
                Column("payload_json", Text, nullable=False),
                Column("occurred_at", DateTime(timezone=True), nullable=False),
                Column("source", String(255), nullable=True),
                Column("metadata_json", Text, nullable=False),
                Column("idempotency_key", String(255), nullable=True),
                Column("status", String(16), nullable=False),
                Column("published_at", DateTime(timezone=True), nullable=True),
                Column("lease_owner", String(255), nullable=True),
                Column("lease_expires_at", DateTime(timezone=True), nullable=True),
                Column("publish_attempts", Integer, nullable=False, default=0),
            )
            metadata.create_all(self.engine)
        except OperationalError as exc:
            raise AtomicStoreUnavailable("Atomic operation storage is unavailable") from exc

    def execute_once(
        self,
        operation_id: str,
        *,
        apply: Callable[[Connection], str],
        outbox_event: Event | None = None,
    ) -> AtomicOperationResult:
        """Commit exactly one database mutation for an operation ID.

        The claim row is inserted before ``apply`` runs.  Therefore a competing
        transaction cannot run its own callback after a committed claim; it
        receives the stored outcome instead.  If ``apply`` raises, the whole
        transaction rolls back and a later attempt may safely retry.
        """
        self._validate_operation_id(operation_id)
        if not callable(apply):
            raise TypeError("apply must be callable")
        if outbox_event is not None:
            self._validate_event(outbox_event)

        try:
            with self.engine.begin() as connection:
                existing = self._existing_result(connection, operation_id, outbox_event)
                if existing is not None:
                    return existing

                # Claim before invoking domain code. The primary key is the
                # cross-worker arbiter; the transaction rolls this claim back
                # if the domain mutation or outbox insert cannot commit.
                connection.execute(insert(self.operations).values(operation_id=operation_id))
                outcome = apply(connection)
                self._validate_outcome(outcome)

                event_id = None
                if outbox_event is not None:
                    event_id = outbox_event.event_id
                    connection.execute(insert(self.outbox).values(**self._outbox_values(operation_id, outbox_event)))

                connection.execute(
                    update(self.operations)
                    .where(self.operations.c.operation_id == operation_id)
                    .values(
                        outcome=outcome,
                        outbox_event_id=event_id,
                        completed_at=datetime.now(timezone.utc),
                    )
                )
                return AtomicOperationResult(operation_id, outcome, False, event_id)
        except IntegrityError:
            # A unique operation claim may have committed in another worker.
            # Do not mistake an unrelated domain constraint for a duplicate.
            existing = self._load_committed_result(operation_id, outbox_event)
            if existing is not None:
                return existing
            raise
        except OperationalError as exc:
            raise AtomicStoreUnavailable("Atomic operation storage is unavailable") from exc

    def pending_outbox(self, *, limit: int = 100) -> list[Event]:
        """Return messages for one serialized publisher.

        Multiple publisher processes must use ``claim_outbox`` instead. A
        plain read cannot reserve a message after the database transaction
        closes, so it is intentionally not presented as a HA work-claim API.
        """
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        try:
            with self.engine.connect() as connection:
                rows = connection.execute(
                    select(self.outbox)
                    .where(self.outbox.c.status == "PENDING")
                    .order_by(self.outbox.c.occurred_at, self.outbox.c.event_id)
                    .limit(limit)
                ).mappings().all()
        except OperationalError as exc:
            raise AtomicStoreUnavailable("Atomic operation storage is unavailable") from exc
        return [self._event_from_row(row) for row in rows]

    def claim_outbox(self, worker_id: str, *, limit: int = 100, lease_seconds: int = 30) -> list[Event]:
        """Atomically lease a disjoint publish batch for one HA worker.

        PostgreSQL's ``FOR UPDATE SKIP LOCKED`` prevents competing workers
        from selecting the same pending rows. The lease survives the select
        transaction, so a crashed worker's work becomes claimable after its
        expiry. Publication remains at-least-once by design.
        """
        self._validate_worker_id(worker_id)
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        if not isinstance(lease_seconds, int) or lease_seconds < 1:
            raise ValueError("lease_seconds must be a positive integer")
        now = datetime.now(timezone.utc)
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        available = or_(
            self.outbox.c.status == "PENDING",
            and_(self.outbox.c.status == "LEASED", self.outbox.c.lease_expires_at < now),
        )
        try:
            with self.engine.begin() as connection:
                rows = connection.execute(
                    select(self.outbox)
                    .where(available)
                    .order_by(self.outbox.c.occurred_at, self.outbox.c.event_id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                ).mappings().all()
                event_ids = [row["event_id"] for row in rows]
                if event_ids:
                    connection.execute(
                        update(self.outbox)
                        .where(self.outbox.c.event_id.in_(event_ids))
                        .values(
                            status="LEASED",
                            lease_owner=worker_id,
                            lease_expires_at=lease_expires_at,
                            publish_attempts=self.outbox.c.publish_attempts + 1,
                        )
                    )
        except OperationalError as exc:
            raise AtomicStoreUnavailable("Atomic operation storage is unavailable") from exc
        return [self._event_from_row(row) for row in rows]

    def mark_published(self, event_id: str, *, worker_id: str | None = None) -> bool:
        """Record broker acceptance after publication; repeated calls are harmless."""
        self._validate_event_id(event_id)
        if worker_id is not None:
            self._validate_worker_id(worker_id)
        try:
            with self.engine.begin() as connection:
                eligible = self.outbox.c.status == "PENDING" if worker_id is None else and_(
                    self.outbox.c.status == "LEASED", self.outbox.c.lease_owner == worker_id
                )
                result = connection.execute(
                    update(self.outbox)
                    .where(and_(self.outbox.c.event_id == event_id, eligible))
                    .values(status="PUBLISHED", published_at=datetime.now(timezone.utc), lease_owner=None, lease_expires_at=None)
                )
        except OperationalError as exc:
            raise AtomicStoreUnavailable("Atomic operation storage is unavailable") from exc
        return result.rowcount == 1

    def release_outbox_lease(self, event_id: str, *, worker_id: str) -> bool:
        """Make a failed publish immediately available to another worker."""
        self._validate_event_id(event_id)
        self._validate_worker_id(worker_id)
        try:
            with self.engine.begin() as connection:
                result = connection.execute(
                    update(self.outbox)
                    .where(and_(self.outbox.c.event_id == event_id, self.outbox.c.status == "LEASED", self.outbox.c.lease_owner == worker_id))
                    .values(status="PENDING", lease_owner=None, lease_expires_at=None)
                )
        except OperationalError as exc:
            raise AtomicStoreUnavailable("Atomic operation storage is unavailable") from exc
        return result.rowcount == 1

    def _existing_result(
        self,
        connection: Connection,
        operation_id: str,
        outbox_event: Event | None,
    ) -> AtomicOperationResult | None:
        row = connection.execute(
            select(self.operations).where(self.operations.c.operation_id == operation_id)
        ).mappings().first()
        if row is None or row["outcome"] is None:
            return None
        self._validate_duplicate_event(row["outbox_event_id"], outbox_event)
        return AtomicOperationResult(operation_id, str(row["outcome"]), True, row["outbox_event_id"])

    def _load_committed_result(
        self,
        operation_id: str,
        outbox_event: Event | None,
    ) -> AtomicOperationResult | None:
        try:
            with self.engine.connect() as connection:
                return self._existing_result(connection, operation_id, outbox_event)
        except OperationalError as exc:
            raise AtomicStoreUnavailable("Atomic operation storage is unavailable") from exc

    def _outbox_values(self, operation_id: str, event: Event) -> dict[str, object]:
        return {
            "event_id": event.event_id,
            "operation_id": operation_id,
            "event_type": event.event_type,
            "entity_id": event.entity_id,
            "entity_type": event.entity_type,
            "payload_json": json.dumps(event.payload, sort_keys=True, default=str),
            "occurred_at": event.occurred_at,
            "source": event.source,
            "metadata_json": json.dumps(event.metadata, sort_keys=True, default=str),
            "idempotency_key": event.idempotency_key,
            "status": "PENDING",
            "published_at": None,
            "lease_owner": None,
            "lease_expires_at": None,
            "publish_attempts": 0,
        }

    @staticmethod
    def _event_from_row(row: object) -> Event:
        return Event(
            event_type=row["event_type"],
            entity_id=row["entity_id"],
            entity_type=row["entity_type"],
            payload=json.loads(row["payload_json"]),
            occurred_at=row["occurred_at"],
            source=row["source"],
            event_id=row["event_id"],
            metadata=json.loads(row["metadata_json"]),
            idempotency_key=row["idempotency_key"],
        )

    @staticmethod
    def _validate_operation_id(operation_id: str) -> None:
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("operation_id must be a non-empty string")

    @staticmethod
    def _validate_event_id(event_id: str) -> None:
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError("event_id must be a non-empty string")

    @staticmethod
    def _validate_worker_id(worker_id: str) -> None:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id must be a non-empty string")

    @classmethod
    def _validate_event(cls, event: Event) -> None:
        if not isinstance(event, Event):
            raise TypeError("outbox_event must be an Event")
        cls._validate_event_id(event.event_id)

    @staticmethod
    def _validate_outcome(outcome: str) -> None:
        if not isinstance(outcome, str) or not outcome.strip():
            raise ValueError("apply must return a non-empty domain outcome string")

    @staticmethod
    def _validate_duplicate_event(existing_event_id: object, event: Event | None) -> None:
        if event is not None and existing_event_id != event.event_id:
            raise OperationIdentityMismatch(
                "A duplicate operation must use the same outbound event identity"
            )


__all__ = [
    "AtomicOperationResult",
    "AtomicStoreUnavailable",
    "OperationIdentityMismatch",
    "PostgresAtomicOperationStore",
]
