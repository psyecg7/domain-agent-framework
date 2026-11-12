"""Inventory-owned durable command acceptance, independent from Kafka results.

The inbox persists an accepted command before a gRPC acknowledgement is sent.
Workers claim commands concurrently. A production Inventory implementation must
still combine its business effect, operation evidence, and result outbox in its
own native transaction; an inbox alone cannot atomically cover an external
side effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Literal, Protocol

from sqlalchemy import Column, DateTime, MetaData, String, Table, Text, and_, create_engine, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from .contracts import KafkaMessage


Acceptance = Literal["ACCEPTED", "DUPLICATE"]


class InventoryCommandInbox(Protocol):
    """Durable acceptance boundary used by the gRPC gateway."""

    def accept(self, command: KafkaMessage) -> Acceptance: ...


class InventoryCommandInboxUnavailable(RuntimeError):
    """The Inventory command inbox could not durably accept or claim work."""


@dataclass(frozen=True)
class AcceptedCommand:
    command: KafkaMessage
    acceptance: Acceptance


class InMemoryInventoryCommandInbox:
    """Thread-safe test adapter; replace with a transactional domain inbox."""

    def __init__(self) -> None:
        self._by_operation: dict[tuple[str, str], KafkaMessage] = {}
        self._lock = Lock()

    def accept(self, command: KafkaMessage) -> Acceptance:
        key = (command.metadata.operation_id, command.metadata.attempt_id)
        with self._lock:
            existing = self._by_operation.get(key)
            if existing is None:
                self._by_operation[key] = command
                return "ACCEPTED"
            if existing.model_dump(mode="json") == command.model_dump(mode="json"):
                return "DUPLICATE"
        raise ValueError("operation_id and attempt_id already identify a different command")

    def accepted(self) -> list[KafkaMessage]:
        with self._lock:
            return list(self._by_operation.values())


class PostgresInventoryCommandInbox:
    """PostgreSQL-backed, multi-worker command inbox for the Inventory domain.

    gRPC acknowledgement is returned only after ``accept`` commits. Multiple
    workers claim independent rows with ``FOR UPDATE SKIP LOCKED``. This store
    deliberately owns command acceptance only; the Inventory domain still has
    to make its stock transition, operation evidence, and result outbox atomic
    in its system of record.
    """

    def __init__(self, database_url: str, *, table_name: str = "kad_inventory_command_inbox") -> None:
        if not isinstance(table_name, str) or not table_name.isidentifier():
            raise ValueError("table_name must be a SQL identifier")
        try:
            self.engine: Engine = create_engine(database_url)
            metadata = MetaData()
            self.commands = Table(
                table_name,
                metadata,
                Column("operation_id", String(255), primary_key=True),
                Column("attempt_id", String(255), primary_key=True),
                Column("event_id", String(255), nullable=False, unique=True),
                Column("command_json", Text, nullable=False),
                Column("status", String(16), nullable=False),
                Column("lease_owner", String(255)),
                Column("lease_expires_at", DateTime(timezone=True)),
                Column("accepted_at", DateTime(timezone=True), nullable=False),
            )
            metadata.create_all(self.engine)
        except SQLAlchemyError as exc:
            raise InventoryCommandInboxUnavailable("Inventory command inbox is unavailable") from exc

    def accept(self, command: KafkaMessage) -> Acceptance:
        serialized = command.model_dump_json()
        try:
            with self.engine.begin() as connection:
                inserted = connection.execute(
                    postgres_insert(self.commands)
                    .values(
                        operation_id=command.metadata.operation_id,
                        attempt_id=command.metadata.attempt_id,
                        event_id=command.event_id,
                        command_json=serialized,
                        status="PENDING",
                        accepted_at=datetime.now(timezone.utc),
                    )
                    .on_conflict_do_nothing(index_elements=["operation_id", "attempt_id"])
                    .returning(self.commands.c.operation_id),
                ).scalar_one_or_none()
                if inserted is not None:
                    return "ACCEPTED"
                existing = connection.execute(
                    select(self.commands.c.command_json).where(and_(
                        self.commands.c.operation_id == command.metadata.operation_id,
                        self.commands.c.attempt_id == command.metadata.attempt_id,
                    )),
                ).scalar_one()
                if str(existing) == serialized:
                    return "DUPLICATE"
        except SQLAlchemyError as exc:
            raise InventoryCommandInboxUnavailable("Inventory command inbox could not record acceptance") from exc
        raise ValueError("operation_id and attempt_id already identify a different command")

    def claim_pending(self, worker_id: str, *, limit: int = 100, lease_seconds: int = 30) -> list[KafkaMessage]:
        if not worker_id or limit < 1 or lease_seconds < 1:
            raise ValueError("worker_id, limit, and lease_seconds must be positive")
        now = datetime.now(timezone.utc)
        eligible = or_(
            self.commands.c.status == "PENDING",
            and_(self.commands.c.status == "LEASED", self.commands.c.lease_expires_at < now),
        )
        try:
            with self.engine.begin() as connection:
                rows = connection.execute(
                    select(self.commands)
                    .where(eligible)
                    .order_by(self.commands.c.accepted_at)
                    .limit(limit)
                    .with_for_update(skip_locked=True),
                ).mappings().all()
                keys = [(row["operation_id"], row["attempt_id"]) for row in rows]
                for operation_id, attempt_id in keys:
                    connection.execute(
                        update(self.commands)
                        .where(and_(self.commands.c.operation_id == operation_id, self.commands.c.attempt_id == attempt_id))
                        .values(
                            status="LEASED", lease_owner=worker_id,
                            lease_expires_at=now + timedelta(seconds=lease_seconds),
                        ),
                    )
        except SQLAlchemyError as exc:
            raise InventoryCommandInboxUnavailable("Inventory command inbox could not claim work") from exc
        return [KafkaMessage.model_validate_json(str(row["command_json"])) for row in rows]

    def mark_completed(self, command: KafkaMessage, *, worker_id: str) -> None:
        self._transition(command, worker_id=worker_id, status="COMPLETED")

    def release_pending(self, command: KafkaMessage, *, worker_id: str) -> None:
        self._transition(command, worker_id=worker_id, status="PENDING")

    def _transition(self, command: KafkaMessage, *, worker_id: str, status: str) -> None:
        try:
            with self.engine.begin() as connection:
                values: dict[str, object] = {"status": status, "lease_owner": None, "lease_expires_at": None}
                updated = connection.execute(
                    update(self.commands)
                    .where(and_(
                        self.commands.c.operation_id == command.metadata.operation_id,
                        self.commands.c.attempt_id == command.metadata.attempt_id,
                        self.commands.c.status == "LEASED",
                        self.commands.c.lease_owner == worker_id,
                    ))
                    .values(**values),
                ).rowcount
                if updated != 1:
                    raise KeyError("Inventory command is not leased by this worker")
        except SQLAlchemyError as exc:
            raise InventoryCommandInboxUnavailable("Inventory command inbox could not change command status") from exc
