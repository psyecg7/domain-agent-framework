"""Durable, domain-owned evidence for Inventory reservation reconciliation.

This is deliberately an Inventory adapter rather than an agent-core primitive.
It preserves the 2S reconciliation vocabulary while storing operation-keyed
effect evidence independently of aggregate inventory state and result events.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import DeltaError


_STATUSES = {"EXISTS", "ABSENT", "CONFLICT", "STILL_UNKNOWN"}


class EvidenceUnavailable(RuntimeError):
    """Persistent evidence could not be read, so Inventory cannot answer."""


class DeltaReservationEvidenceStore:
    """Operation-keyed reservation evidence persisted in a Delta table.

    For serialized writes, `record` is idempotent for the same operation and
    status. Contradictory evidence is retained as the authoritative `CONFLICT`
    outcome rather than silently choosing a winner.

    This reference adapter has no cross-process compare-and-swap guarantee;
    deployments that permit concurrent writers need a storage-level concurrency
    contract before treating it as an execution ledger.
    """

    def __init__(self, table_path: str | Path) -> None:
        self.table_path = Path(table_path)
        self.table_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_table()

    def record(
        self,
        operation_id: str,
        status: str,
        *,
        evidence: dict[str, Any] | None = None,
    ) -> bool:
        if not operation_id.strip():
            raise ValueError("operation_id must not be empty")
        if status not in _STATUSES - {"ABSENT"}:
            raise ValueError(f"Cannot persist reservation status {status!r}")

        records = self._records()
        existing = records[records["operation_id"] == operation_id]
        if not existing.empty:
            prior_status = str(existing.iloc[0]["reservation_status"])
            if prior_status == status:
                return False
            records.loc[
                records["operation_id"] == operation_id,
                ["reservation_status", "evidence", "recorded_at"],
            ] = [
                "CONFLICT",
                json.dumps(
                    {"previous_status": prior_status, "new_status": status, "evidence": evidence or {}},
                    sort_keys=True,
                ),
                datetime.now(timezone.utc).isoformat(),
            ]
            self._write(records)
            return True

        record = pd.DataFrame([
            {
                "operation_id": operation_id,
                "reservation_status": status,
                "evidence": json.dumps(evidence or {}, sort_keys=True, default=str),
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
        ])
        self._write(pd.concat([records, record], ignore_index=True))
        return True

    def reconcile(self, operation_id: str) -> str:
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ValueError("operation_id must be a non-empty string")
        try:
            records = self._records()
        except (DeltaError, OSError) as exc:
            raise EvidenceUnavailable("Reservation evidence is unavailable") from exc
        matches = records.query("operation_id == @operation_id")
        if matches.empty:
            return "ABSENT"
        return str(matches.iloc[0]["reservation_status"])

    def evidence_for(self, operation_id: str) -> dict[str, Any] | None:
        matches = self._records().query("operation_id == @operation_id")
        if matches.empty:
            return None
        return json.loads(str(matches.iloc[0]["evidence"]))

    def _ensure_table(self) -> None:
        if self.table_path.exists():
            return
        write_deltalake(
            self.table_path,
            data=pd.DataFrame([
                {
                    "operation_id": "__schema__",
                    "reservation_status": "__meta__",
                    "evidence": "{}",
                    "recorded_at": "1970-01-01T00:00:00+00:00",
                }
            ]),
            mode="append",
        )

    def _records(self) -> pd.DataFrame:
        records = DeltaTable(self.table_path).to_pandas()
        return records[records["operation_id"] != "__schema__"].copy()

    def _write(self, records: pd.DataFrame) -> None:
        schema = pd.DataFrame([
            {
                "operation_id": "__schema__",
                "reservation_status": "__meta__",
                "evidence": "{}",
                "recorded_at": "1970-01-01T00:00:00+00:00",
            }
        ])
        write_deltalake(self.table_path, data=pd.concat([schema, records], ignore_index=True), mode="overwrite")


class InventoryReservationAuthority:
    """Inventory-facing authority backed by durable reservation evidence."""

    def __init__(self, evidence_store: DeltaReservationEvidenceStore) -> None:
        self.evidence_store = evidence_store

    def reserve(self, operation_id: str, *, evidence: dict[str, Any] | None = None) -> bool:
        return self.evidence_store.record(operation_id, "EXISTS", evidence=evidence)

    def reconcile(self, operation_id: str) -> str:
        try:
            return self.evidence_store.reconcile(operation_id)
        except EvidenceUnavailable:
            # This is authoritative uncertainty, not a transport exception.
            return "STILL_UNKNOWN"
