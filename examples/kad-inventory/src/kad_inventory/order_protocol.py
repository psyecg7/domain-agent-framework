"""Durable asynchronous Order ↔ Inventory Kafka protocol.

Topics are delivery lanes.  Correlation, causation, operation, and attempt
identity travel in every message so results can be applied after a restart,
redelivery, or out-of-order arrival on a different topic.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from sqlalchemy import Column, DateTime, MetaData, String, Table, Text, and_, create_engine, insert, or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from .contracts import (
    GuardedCommand, InventoryReallocated, KafkaMessage, MessageMetadata,
    MoveOrderIntent, ReallocationFailed,
)
from .events import EventPublisher, INVENTORY_COMMANDS_TOPIC, ORDER_RESULTS_TOPIC
from .state import InMemoryInventoryState, InvariantViolationError


@dataclass(frozen=True)
class OrderProcessSnapshot:
    correlation_id: str
    order_id: str
    operation_id: str
    attempt_id: str
    request_event_id: str
    status: Literal["PENDING_INVENTORY", "SUCCEEDED", "CONFLICT"]
    applied_event_ids: tuple[str, ...]


class OrderProcessStoreUnavailable(RuntimeError):
    """The shared Order process/outbox storage is unavailable."""


def _apply_result(current: OrderProcessSnapshot, result: KafkaMessage) -> tuple[OrderProcessSnapshot, bool]:
    """Validate lineage and produce the next durable Order process snapshot."""
    if result.event_id in current.applied_event_ids:
        return current, False
    if (
        result.metadata.causation_id != current.request_event_id
        or result.metadata.operation_id != current.operation_id
        or result.metadata.attempt_id != current.attempt_id
    ):
        raise ValueError("Inventory result does not belong to the current Order operation attempt")
    values = {**asdict(current), "applied_event_ids": current.applied_event_ids + (result.event_id,)}
    if current.status == "PENDING_INVENTORY":
        if result.event_type == "InventoryReallocated":
            values["status"] = "SUCCEEDED"
        elif result.event_type == "ReallocationFailed":
            values["status"] = "CONFLICT"
        else:
            raise ValueError(f"Unsupported Inventory result {result.event_type!r}")
    # A distinct terminal result cannot overwrite a prior terminal outcome.
    return OrderProcessSnapshot(**values), True


class SqliteOrderProcessStore:
    """Order-owned durable correlation state plus a local transactional outbox.

    SQLite makes restart behavior executable in this reference.  A scaled
    deployment replaces it with a shared, transactional store; it must retain
    the same unique correlation/operation identity and atomic outbox claim.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        connection = self._connection()
        try:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS kad_order_processes (
                    correlation_id TEXT PRIMARY KEY,
                    snapshot_json TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    request_event_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    lease_owner TEXT,
                    lease_expires_at INTEGER
                )
            """)
            connection.commit()
        finally:
            connection.close()

    def create(self, snapshot: OrderProcessSnapshot, request: KafkaMessage) -> bool:
        connection = self._connection()
        try:
            connection.execute(
                """INSERT INTO kad_order_processes
                   (correlation_id, snapshot_json, request_json, request_event_id, status)
                   VALUES (?, ?, ?, ?, 'PENDING')""",
                (snapshot.correlation_id, self._encode_snapshot(snapshot), request.model_dump_json(), request.event_id),
            )
            connection.commit()
            return True
        except sqlite3.IntegrityError:
            connection.rollback()
            return False
        finally:
            connection.close()

    def load(self, correlation_id: str) -> OrderProcessSnapshot:
        connection = self._connection()
        try:
            row = connection.execute(
                "SELECT snapshot_json FROM kad_order_processes WHERE correlation_id = ?", (correlation_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise KeyError(f"No Order process for correlation_id {correlation_id!r}")
        return self._decode_snapshot(row[0])

    def claim_pending(self, worker_id: str, *, limit: int = 100, lease_seconds: int = 30) -> list[KafkaMessage]:
        now = int(datetime.now(timezone.utc).timestamp())
        lease_expires_at = now + lease_seconds
        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """SELECT request_json, request_event_id FROM kad_order_processes
                   WHERE status = 'PENDING' OR (status = 'LEASED' AND lease_expires_at < ?)
                   ORDER BY rowid LIMIT ?""",
                (now, limit),
            ).fetchall()
            for _, event_id in rows:
                connection.execute(
                    """UPDATE kad_order_processes
                       SET status = 'LEASED', lease_owner = ?, lease_expires_at = ?
                       WHERE request_event_id = ?""",
                    (worker_id, lease_expires_at, event_id),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return [KafkaMessage.model_validate_json(row[0]) for row in rows]

    def mark_published(self, event_id: str, *, worker_id: str) -> None:
        connection = self._connection()
        try:
            updated = connection.execute(
                """UPDATE kad_order_processes SET status = 'PUBLISHED', lease_owner = NULL, lease_expires_at = NULL
                   WHERE request_event_id = ? AND status = 'LEASED' AND lease_owner = ?""",
                (event_id, worker_id),
            ).rowcount
            if updated != 1:
                raise KeyError(f"No leased Order request for event_id {event_id!r}")
            connection.commit()
        finally:
            connection.close()

    def release_pending(self, event_id: str, *, worker_id: str) -> None:
        connection = self._connection()
        try:
            connection.execute(
                """UPDATE kad_order_processes SET status = 'PENDING', lease_owner = NULL, lease_expires_at = NULL
                   WHERE request_event_id = ? AND status = 'LEASED' AND lease_owner = ?""",
                (event_id, worker_id),
            )
            connection.commit()
        finally:
            connection.close()

    def apply_result(self, result: KafkaMessage) -> bool:
        """Apply an Inventory result once, rejecting mismatched lineage."""
        connection = self._connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT snapshot_json FROM kad_order_processes WHERE correlation_id = ?",
                (result.metadata.correlation_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"No Order process for correlation_id {result.metadata.correlation_id!r}")
            updated, applied = _apply_result(self._decode_snapshot(row[0]), result)
            if not applied:
                connection.commit()
                return False
            connection.execute(
                "UPDATE kad_order_processes SET snapshot_json = ? WHERE correlation_id = ?",
                (self._encode_snapshot(updated), updated.correlation_id),
            )
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _connection(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, isolation_level=None)

    @staticmethod
    def _encode_snapshot(snapshot: OrderProcessSnapshot) -> str:
        value = asdict(snapshot)
        value["applied_event_ids"] = list(snapshot.applied_event_ids)
        return json.dumps(value, sort_keys=True)

    @staticmethod
    def _decode_snapshot(value: str) -> OrderProcessSnapshot:
        decoded = json.loads(value)
        decoded["applied_event_ids"] = tuple(decoded["applied_event_ids"])
        return OrderProcessSnapshot(**decoded)


class PostgresOrderProcessStore:
    """Shared PostgreSQL Order process state and HA-safe transactional outbox.

    One transaction inserts the Order process and its outbound Inventory
    request. ``claim_pending`` leases rows using ``FOR UPDATE SKIP LOCKED``;
    distinct publisher replicas therefore cannot publish the same leased row
    concurrently. At-least-once broker delivery remains intentional.
    """

    def __init__(self, database_url: str, *, table_prefix: str = "kad_order") -> None:
        if not isinstance(table_prefix, str) or not table_prefix.isidentifier():
            raise ValueError("table_prefix must be a SQL identifier")
        try:
            self.engine: Engine = create_engine(database_url)
            metadata = MetaData()
            self.processes = Table(
                f"{table_prefix}_processes", metadata,
                Column("correlation_id", String(255), primary_key=True),
                Column("snapshot_json", Text, nullable=False),
            )
            self.outbox = Table(
                f"{table_prefix}_outbox", metadata,
                Column("event_id", String(255), primary_key=True),
                Column("correlation_id", String(255), nullable=False, unique=True),
                Column("message_json", Text, nullable=False),
                Column("status", String(16), nullable=False),
                Column("lease_owner", String(255)),
                Column("lease_expires_at", DateTime(timezone=True)),
            )
            metadata.create_all(self.engine)
        except SQLAlchemyError as exc:
            raise OrderProcessStoreUnavailable("Order process storage is unavailable") from exc

    def create(self, snapshot: OrderProcessSnapshot, request: KafkaMessage) -> bool:
        try:
            with self.engine.begin() as connection:
                connection.execute(insert(self.processes).values(
                    correlation_id=snapshot.correlation_id,
                    snapshot_json=SqliteOrderProcessStore._encode_snapshot(snapshot),
                ))
                connection.execute(insert(self.outbox).values(
                    event_id=request.event_id,
                    correlation_id=snapshot.correlation_id,
                    message_json=request.model_dump_json(),
                    status="PENDING",
                ))
            return True
        except IntegrityError:
            return False
        except SQLAlchemyError as exc:
            raise OrderProcessStoreUnavailable("Order process/outbox transaction could not commit") from exc

    def load(self, correlation_id: str) -> OrderProcessSnapshot:
        try:
            with self.engine.connect() as connection:
                value = connection.execute(
                    select(self.processes.c.snapshot_json).where(self.processes.c.correlation_id == correlation_id),
                ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise OrderProcessStoreUnavailable("Order process storage is unavailable") from exc
        if value is None:
            raise KeyError(f"No Order process for correlation_id {correlation_id!r}")
        return SqliteOrderProcessStore._decode_snapshot(str(value))

    def claim_pending(self, worker_id: str, *, limit: int = 100, lease_seconds: int = 30) -> list[KafkaMessage]:
        if not worker_id or limit < 1 or lease_seconds < 1:
            raise ValueError("worker_id, limit, and lease_seconds must be positive")
        now = datetime.now(timezone.utc)
        eligible = or_(
            self.outbox.c.status == "PENDING",
            and_(self.outbox.c.status == "LEASED", self.outbox.c.lease_expires_at < now),
        )
        try:
            with self.engine.begin() as connection:
                rows = connection.execute(
                    select(self.outbox)
                    .where(eligible)
                    .order_by(self.outbox.c.event_id)
                    .limit(limit)
                    .with_for_update(skip_locked=True),
                ).mappings().all()
                event_ids = [row["event_id"] for row in rows]
                if event_ids:
                    connection.execute(
                        update(self.outbox)
                        .where(self.outbox.c.event_id.in_(event_ids))
                        .values(
                            status="LEASED", lease_owner=worker_id,
                            lease_expires_at=now + timedelta(seconds=lease_seconds),
                        ),
                    )
        except SQLAlchemyError as exc:
            raise OrderProcessStoreUnavailable("Order outbox could not be claimed") from exc
        return [KafkaMessage.model_validate_json(str(row["message_json"])) for row in rows]

    def mark_published(self, event_id: str, *, worker_id: str) -> None:
        try:
            with self.engine.begin() as connection:
                updated = connection.execute(
                    update(self.outbox)
                    .where(and_(self.outbox.c.event_id == event_id, self.outbox.c.status == "LEASED", self.outbox.c.lease_owner == worker_id))
                    .values(status="PUBLISHED", lease_owner=None, lease_expires_at=None),
                ).rowcount
                if updated != 1:
                    raise KeyError(f"No leased Order request for event_id {event_id!r}")
        except OperationalError as exc:
            raise OrderProcessStoreUnavailable("Order outbox could not record broker acceptance") from exc

    def release_pending(self, event_id: str, *, worker_id: str) -> None:
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    update(self.outbox)
                    .where(and_(self.outbox.c.event_id == event_id, self.outbox.c.status == "LEASED", self.outbox.c.lease_owner == worker_id))
                    .values(status="PENDING", lease_owner=None, lease_expires_at=None),
                )
        except OperationalError as exc:
            raise OrderProcessStoreUnavailable("Order outbox could not release its lease") from exc

    def apply_result(self, result: KafkaMessage) -> bool:
        try:
            with self.engine.begin() as connection:
                value = connection.execute(
                    select(self.processes.c.snapshot_json)
                    .where(self.processes.c.correlation_id == result.metadata.correlation_id)
                    .with_for_update(),
                ).scalar_one_or_none()
                if value is None:
                    raise KeyError(f"No Order process for correlation_id {result.metadata.correlation_id!r}")
                updated, applied = _apply_result(SqliteOrderProcessStore._decode_snapshot(str(value)), result)
                if applied:
                    connection.execute(
                        update(self.processes)
                        .where(self.processes.c.correlation_id == updated.correlation_id)
                        .values(snapshot_json=SqliteOrderProcessStore._encode_snapshot(updated)),
                    )
                return applied
        except OperationalError as exc:
            raise OrderProcessStoreUnavailable("Order process result could not be recorded") from exc


class OrderInventoryCoordinator:
    """Owns request correlation; it never waits in memory for an Inventory reply."""

    def __init__(self, store: SqliteOrderProcessStore | PostgresOrderProcessStore, publisher: EventPublisher) -> None:
        self.store = store
        self.publisher = publisher

    def submit(self, intent: MoveOrderIntent) -> OrderProcessSnapshot:
        correlation_id = f"order:{intent.order_id}"
        operation_id = f"{intent.order_id}:inventory-reallocation"
        attempt_id = f"{operation_id}:attempt-1"
        request = KafkaMessage(
            event_type="InventoryReallocationRequested",
            entity_id=intent.order_id,
            payload=intent.model_dump(),
            metadata=MessageMetadata(
                correlation_id=correlation_id,
                causation_id=f"order-submitted:{intent.order_id}",
                operation_id=operation_id,
                attempt_id=attempt_id,
            ),
        )
        snapshot = OrderProcessSnapshot(
            correlation_id=correlation_id,
            order_id=intent.order_id,
            operation_id=operation_id,
            attempt_id=attempt_id,
            request_event_id=request.event_id,
            status="PENDING_INVENTORY",
            applied_event_ids=(),
        )
        if self.store.create(snapshot, request):
            self.dispatch_pending()
        return self.store.load(correlation_id)

    def dispatch_pending(self, *, worker_id: str = "kad-order-outbox") -> int:
        """Retry only publication of already-persisted requests after a crash."""
        count = 0
        for request in self.store.claim_pending(worker_id):
            payload = MoveOrderIntent.model_validate(request.payload)
            try:
                self.publisher.publish(
                    request.model_dump(),
                    topic=INVENTORY_COMMANDS_TOPIC,
                    key=f"{payload.sku}|{payload.target_warehouse}",
                )
            except Exception:
                self.store.release_pending(request.event_id, worker_id=worker_id)
                raise
            self.store.mark_published(request.event_id, worker_id=worker_id)
            count += 1
        return count

    def observe_inventory_result(self, record: dict) -> bool:
        return self.store.apply_result(KafkaMessage.model_validate(record))


class InventoryReallocationCommandHandler:
    """Consumes inventory-keyed commands and emits order-keyed results."""

    def __init__(self, state: InMemoryInventoryState, publisher: EventPublisher) -> None:
        self.state = state
        self.publisher = publisher

    def handle(self, record: dict) -> KafkaMessage:
        request = KafkaMessage.model_validate(record)
        if request.event_type != "InventoryReallocationRequested":
            raise ValueError("Inventory handler accepts only reallocation requests")
        intent = MoveOrderIntent.model_validate(request.payload)
        command = GuardedCommand(**intent.model_dump())
        try:
            result = self.state.execute_with_predicates(command)
            event_type = "InventoryReallocated"
            payload = InventoryReallocated(
                order_id=intent.order_id,
                sku=intent.sku,
                target_warehouse=intent.target_warehouse,
                quantity=intent.quantity,
                state_version=result.state_version,
            ).model_dump()
        except InvariantViolationError as exc:
            event_type = "ReallocationFailed"
            payload = exc.failure.model_dump()
        response = KafkaMessage(
            event_type=event_type,
            entity_id=intent.order_id,
            payload=payload,
            metadata=MessageMetadata(
                correlation_id=request.metadata.correlation_id,
                causation_id=request.event_id,
                operation_id=request.metadata.operation_id,
                attempt_id=request.metadata.attempt_id,
            ),
        )
        self.publisher.publish(
            response.model_dump(),
            topic=ORDER_RESULTS_TOPIC,
            key=intent.order_id,
        )
        return response
