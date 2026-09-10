"""Delta-backed Inventory reference for domain-owned execution preconditions.

This is deliberately a concrete Inventory example, not a core state or
workflow primitive.  It shows where the business TOCTOU check belongs: at the
target domain's conditional write, after the Executor has verified the signed
command but before Inventory records a reservation effect.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import DeltaError

from .authorization import ExecutionCommand


class InventoryUnavailable(RuntimeError):
    """Inventory could not safely read or conditionally update its stock."""


@dataclass(frozen=True)
class InventorySnapshot:
    product_id: str
    available: int
    version: int


class DeltaInventoryReservationHandler:
    """Inventory's native conditional reservation effect.

    ``reserve`` uses one Delta conditional update to check the stock version,
    the availability observed by Policy, and the requested quantity.  A row
    that no longer meets any one of those conditions yields ``CONFLICT`` and
    produces no stock mutation.

    This reference models the stock effect only.  Production Inventory must
    also make its operation/effect evidence durable in a transactionally
    consistent way with the chosen system of record; the existing reservation
    evidence adapter remains a separate reconciliation example.
    """

    def __init__(self, table_path: str | Path) -> None:
        self.table_path = Path(table_path)
        self.table_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._ensure_table()
        except (DeltaError, OSError) as exc:
            raise InventoryUnavailable("Inventory stock storage is unavailable") from exc

    def seed(self, product_id: str, *, available: int, version: int = 1) -> None:
        if not isinstance(product_id, str) or not product_id.strip():
            raise ValueError("product_id must be a non-empty string")
        if not isinstance(available, int) or available < 0:
            raise ValueError("available must be a non-negative integer")
        if not isinstance(version, int) or version < 1:
            raise ValueError("version must be a positive integer")
        try:
            table = DeltaTable(self.table_path)
            existing = table.to_pandas().query("product_id == @product_id")
            if not existing.empty:
                raise ValueError(f"Inventory already has product {product_id!r}")
            write_deltalake(
                self.table_path,
                data=pd.DataFrame([{"product_id": product_id, "available": available, "version": version}]),
                mode="append",
            )
        except (DeltaError, OSError) as exc:
            raise InventoryUnavailable("Inventory stock storage is unavailable") from exc

    def snapshot(self, product_id: str) -> InventorySnapshot:
        if not isinstance(product_id, str) or not product_id.strip():
            raise ValueError("product_id must be a non-empty string")
        try:
            matches = DeltaTable(self.table_path).to_pandas().query("product_id == @product_id")
        except (DeltaError, OSError) as exc:
            raise InventoryUnavailable("Inventory stock storage is unavailable") from exc
        if matches.empty:
            raise ValueError(f"Unknown inventory product {product_id!r}")
        row = matches.iloc[0]
        return InventorySnapshot(product_id, int(row["available"]), int(row["version"]))

    def preconditions_for(self, product_id: str) -> dict[str, int]:
        snapshot = self.snapshot(product_id)
        return {"inventory_version": snapshot.version, "available": snapshot.available}

    def reserve(self, command: ExecutionCommand) -> str:
        """Return ``SUCCEEDED`` or the domain result ``CONFLICT``.

        The caller has already verified the command signature.  Inventory
        independently validates the command's business shape and then uses
        the signed preconditions in the native atomic write.
        """
        if command.action_type != "RESERVE" or command.entity_type != "inventory":
            raise ValueError("Inventory only accepts RESERVE commands for inventory entities")
        quantity = command.parameters.get("quantity")
        expected_version = command.preconditions.get("inventory_version")
        expected_available = command.preconditions.get("available")
        if (
            not isinstance(quantity, int)
            or quantity < 1
            or not isinstance(expected_version, int)
            or not isinstance(expected_available, int)
            or expected_available < quantity
        ):
            return "CONFLICT"
        product = self._sql_string(command.entity_id)
        predicate = (
            f"product_id = {product} AND version = {expected_version} "
            f"AND available = {expected_available} AND available >= {quantity}"
        )
        try:
            result = DeltaTable(self.table_path).update(
                updates={"available": f"available - {quantity}", "version": "version + 1"},
                predicate=predicate,
            )
        except (DeltaError, OSError) as exc:
            raise InventoryUnavailable("Inventory stock storage is unavailable") from exc
        return "SUCCEEDED" if result.get("num_updated_rows", 0) == 1 else "CONFLICT"

    def __call__(self, command: ExecutionCommand) -> str:
        """Allow direct use as an ``AuthorizedCommandExecutor`` effect handler."""
        return self.reserve(command)

    def _ensure_table(self) -> None:
        if self.table_path.exists():
            return
        write_deltalake(
            self.table_path,
            data=pd.DataFrame([{"product_id": "__schema__", "available": 0, "version": 0}]),
            mode="append",
        )

    @staticmethod
    def _sql_string(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"
