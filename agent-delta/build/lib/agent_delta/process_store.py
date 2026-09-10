"""Durable storage for an application's purchase-process snapshot contract.

The adapter deliberately persists a structural snapshot rather than introducing
an agent-core Process abstraction. A domain process remains responsible for its
own state machine and reconstruction rules.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import pandas as pd
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import DeltaError


class ProcessStoreUnavailable(RuntimeError):
    """A Delta operational failure prevented durable process reconstruction."""


@dataclass
class DeltaProcessSnapshot:
    """Structural counterpart of the application-owned ProcessSnapshot value."""

    process_id: str
    order_id: str
    product_id: str
    correlation_id: str
    status: str
    facts: dict[str, str] = field(default_factory=dict)
    operations: dict[str, dict[str, Any]] = field(default_factory=dict)
    applied_event_ids: set[str] = field(default_factory=set)
    recovery_reason: str | None = None


class SnapshottingProcess(Protocol):
    process_id: str

    def snapshot(self) -> Any:
        ...


class DeltaProcessStore:
    """Persist application-owned process snapshots keyed by ``process_id``.

    This store is durable across restart but has no cross-process
    compare-and-swap/write-serialization guarantee. If it cannot load a
    snapshot, it raises ``ProcessStoreUnavailable``: callers must block startup
    or retry at their infrastructure boundary, never substitute a new process.
    """

    def __init__(self, table_path: str | Path) -> None:
        self.table_path = Path(table_path)
        self.table_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._ensure_table()
        except (DeltaError, OSError) as exc:
            raise ProcessStoreUnavailable("Process storage is unavailable") from exc

    def save(self, process: SnapshottingProcess) -> None:
        snapshot = process.snapshot()
        process_id = getattr(snapshot, "process_id", None)
        if not isinstance(process_id, str) or not process_id.strip():
            raise ValueError("Process snapshot must have a non-empty process_id")
        record = self._to_record(snapshot)
        try:
            records = self._records()
            records = records[records["process_id"] != process_id]
            self._write(pd.concat([records, pd.DataFrame([record])], ignore_index=True))
        except (DeltaError, OSError) as exc:
            raise ProcessStoreUnavailable("Process storage is unavailable") from exc

    def load(self, process_id: str) -> DeltaProcessSnapshot:
        if not isinstance(process_id, str) or not process_id.strip():
            raise ValueError("process_id must be a non-empty string")
        try:
            matches = self._records().query("process_id == @process_id")
        except (DeltaError, OSError) as exc:
            raise ProcessStoreUnavailable("Process storage is unavailable") from exc
        if matches.empty:
            raise KeyError(process_id)
        return self._from_record(dict(matches.iloc[0]))

    @staticmethod
    def _to_record(snapshot: Any) -> dict[str, str]:
        return {
            "process_id": snapshot.process_id,
            "order_id": snapshot.order_id,
            "product_id": snapshot.product_id,
            "correlation_id": snapshot.correlation_id,
            "status": snapshot.status,
            "facts": json.dumps(dict(snapshot.facts), sort_keys=True),
            "operations": json.dumps(dict(snapshot.operations), sort_keys=True, default=str),
            "applied_event_ids": json.dumps(sorted(snapshot.applied_event_ids)),
            "recovery_reason": json.dumps(snapshot.recovery_reason),
        }

    @staticmethod
    def _from_record(record: dict[str, Any]) -> DeltaProcessSnapshot:
        return DeltaProcessSnapshot(
            process_id=str(record["process_id"]),
            order_id=str(record["order_id"]),
            product_id=str(record["product_id"]),
            correlation_id=str(record["correlation_id"]),
            status=str(record["status"]),
            facts=dict(json.loads(str(record["facts"]))),
            operations={key: dict(value) for key, value in json.loads(str(record["operations"])).items()},
            applied_event_ids=set(json.loads(str(record["applied_event_ids"]))),
            recovery_reason=json.loads(str(record["recovery_reason"])),
        )

    def _ensure_table(self) -> None:
        if self.table_path.exists():
            return
        write_deltalake(self.table_path, data=pd.DataFrame([self._schema_record()]), mode="append")

    def _records(self) -> pd.DataFrame:
        records = DeltaTable(self.table_path).to_pandas()
        return records[records["process_id"] != "__schema__"].copy()

    def _write(self, records: pd.DataFrame) -> None:
        write_deltalake(
            self.table_path,
            data=pd.concat([pd.DataFrame([self._schema_record()]), records], ignore_index=True),
            mode="overwrite",
        )

    @staticmethod
    def _schema_record() -> dict[str, str]:
        return {
            "process_id": "__schema__",
            "order_id": "",
            "product_id": "",
            "correlation_id": "",
            "status": "",
            "facts": "{}",
            "operations": "{}",
            "applied_event_ids": "[]",
            "recovery_reason": "null",
        }
