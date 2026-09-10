"""Postgres-backed Inventory reservation authority.

Stock and operation evidence live in one database transaction.  This is a
concrete domain adapter; it intentionally does not create a generic process,
precondition, or execution primitive in ``agent_core``.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Column, Integer, MetaData, String, Table, and_, create_engine, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError


class InventoryStorageUnavailable(RuntimeError):
    """Inventory's transactional system of record could not be used."""


@dataclass(frozen=True)
class InventoryStockSnapshot:
    product_id: str
    available: int
    version: int


class PostgresInventoryReservationAuthority:
    """Inventory-owned conditional stock transition plus durable operation evidence.

    ``reserve`` either updates stock and inserts ``EXISTS`` evidence in the
    same SQL transaction, or inserts ``CONFLICT`` evidence without changing
    stock.  A database failure rolls both writes back; it never creates a
    fresh reservation state as a fallback.
    """

    def __init__(self, database_url: str, *, table_prefix: str = "inventory") -> None:
        if not isinstance(table_prefix, str) or not table_prefix.isidentifier():
            raise ValueError("table_prefix must be a SQL identifier")
        try:
            self.engine: Engine = create_engine(database_url)
            metadata = MetaData()
            self.stock = Table(
                f"{table_prefix}_stock",
                metadata,
                Column("product_id", String(255), primary_key=True),
                Column("available", Integer, nullable=False),
                Column("version", Integer, nullable=False),
            )
            self.effects = Table(
                f"{table_prefix}_reservation_effects",
                metadata,
                Column("operation_id", String(255), primary_key=True),
                Column("product_id", String(255), nullable=False),
                Column("outcome", String(32), nullable=False),
            )
            metadata.create_all(self.engine)
        except SQLAlchemyError as exc:
            raise InventoryStorageUnavailable("Inventory storage is unavailable") from exc

    def seed(self, product_id: str, *, available: int, version: int = 1) -> None:
        self._validate_product(product_id)
        if not isinstance(available, int) or available < 0:
            raise ValueError("available must be a non-negative integer")
        if not isinstance(version, int) or version < 1:
            raise ValueError("version must be a positive integer")
        try:
            with self.engine.begin() as connection:
                connection.execute(insert(self.stock).values(product_id=product_id, available=available, version=version))
        except SQLAlchemyError as exc:
            raise InventoryStorageUnavailable("Inventory storage is unavailable") from exc

    def snapshot(self, product_id: str) -> InventoryStockSnapshot:
        self._validate_product(product_id)
        try:
            with self.engine.connect() as connection:
                row = connection.execute(select(self.stock).where(self.stock.c.product_id == product_id)).mappings().first()
        except SQLAlchemyError as exc:
            raise InventoryStorageUnavailable("Inventory storage is unavailable") from exc
        if row is None:
            raise ValueError(f"Unknown inventory product {product_id!r}")
        return InventoryStockSnapshot(product_id, int(row["available"]), int(row["version"]))

    def preconditions_for(self, product_id: str) -> dict[str, int]:
        snapshot = self.snapshot(product_id)
        return {"inventory_version": snapshot.version, "available": snapshot.available}

    def reserve(self, operation_id: str, product_id: str, quantity: int, *, preconditions: dict[str, int]) -> str:
        self._validate_operation(operation_id)
        self._validate_product(product_id)
        expected_version = preconditions.get("inventory_version")
        expected_available = preconditions.get("available")
        if not isinstance(quantity, int) or quantity < 1:
            raise ValueError("quantity must be a positive integer")
        if not isinstance(expected_version, int) or not isinstance(expected_available, int):
            raise ValueError("Inventory preconditions must contain integer version and available values")
        try:
            with self.engine.begin() as connection:
                prior = connection.execute(
                    select(self.effects.c.outcome).where(self.effects.c.operation_id == operation_id)
                ).scalar_one_or_none()
                if prior is not None:
                    return "SUCCEEDED" if prior == "EXISTS" else str(prior)
                if expected_available < quantity:
                    connection.execute(insert(self.effects).values(operation_id=operation_id, product_id=product_id, outcome="CONFLICT"))
                    return "CONFLICT"
                mutation = connection.execute(
                    update(self.stock)
                    .where(
                        and_(
                            self.stock.c.product_id == product_id,
                            self.stock.c.version == expected_version,
                            self.stock.c.available == expected_available,
                            self.stock.c.available >= quantity,
                        )
                    )
                    .values(available=self.stock.c.available - quantity, version=self.stock.c.version + 1)
                )
                if mutation.rowcount == 1:
                    connection.execute(insert(self.effects).values(operation_id=operation_id, product_id=product_id, outcome="EXISTS"))
                    return "SUCCEEDED"
                connection.execute(insert(self.effects).values(operation_id=operation_id, product_id=product_id, outcome="CONFLICT"))
                return "CONFLICT"
        except SQLAlchemyError as exc:
            raise InventoryStorageUnavailable("Inventory storage is unavailable") from exc

    def reconcile(self, operation_id: str) -> str:
        self._validate_operation(operation_id)
        try:
            with self.engine.connect() as connection:
                outcome = connection.execute(
                    select(self.effects.c.outcome).where(self.effects.c.operation_id == operation_id)
                ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise InventoryStorageUnavailable("Inventory storage is unavailable") from exc
        return "ABSENT" if outcome is None else str(outcome)

    @staticmethod
    def _validate_product(product_id: str) -> None:
        if not isinstance(product_id, str) or not product_id.strip():
            raise ValueError("product_id must be a non-empty string")

    @staticmethod
    def _validate_operation(operation_id: str) -> None:
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("operation_id must be a non-empty string")
