"""Delta-backed replay ledger for a separately deployed Executor service."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pandas as pd
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import DeltaError


class ReplayStoreUnavailable(RuntimeError):
    """Executor cannot safely decide whether an authorization was replayed."""


class DeltaReplayStore:
    """Durable authorization-ID replay ledger.

    claim() is protected by an in-process lock. This closes the concurrent-
    thread race that a threaded Executor server (e.g. executor_server's
    ThreadingHTTPServer) would otherwise hit if two submissions of the same
    authorization arrived at once: both could previously observe "not yet
    claimed" before either wrote, defeating single-use. The lock makes the
    check-then-write atomic within this process.

    This store still has no cross-process compare-and-swap guarantee. A
    multi-process/multi-replica Executor deployment must serialize a key
    through its transport (e.g. partition by authorization_id) or use a
    replay store with a genuinely atomic conditional-write operation across
    processes — an in-process lock cannot provide that.
    """

    def __init__(self, table_path: str | Path) -> None:
        self.table_path = Path(table_path)
        self.table_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        try:
            self._ensure_table()
        except (DeltaError, OSError) as exc:
            raise ReplayStoreUnavailable("Executor replay storage is unavailable") from exc

    def claim(self, authorization_id: str, expires_at: int) -> bool:
        if not isinstance(authorization_id, str) or not authorization_id.strip():
            raise ValueError("authorization_id must be a non-empty string")
        if not isinstance(expires_at, int):
            raise TypeError("expires_at must be an integer epoch timestamp")
        try:
            with self._lock:
                now = int(time.time())
                records = self._records()
                records = records[records["expires_at"] >= now]
                if not records[records["authorization_id"] == authorization_id].empty:
                    return False
                record = pd.DataFrame([{"authorization_id": authorization_id, "expires_at": expires_at}])
                self._write(pd.concat([records, record], ignore_index=True))
                return True
        except (DeltaError, OSError) as exc:
            raise ReplayStoreUnavailable("Executor replay storage is unavailable") from exc

    def _ensure_table(self) -> None:
        if not self.table_path.exists():
            write_deltalake(self.table_path, data=pd.DataFrame([self._schema_record()]), mode="append")

    def _records(self) -> pd.DataFrame:
        records = DeltaTable(self.table_path).to_pandas()
        return records[records["authorization_id"] != "__schema__"].copy()

    def _write(self, records: pd.DataFrame) -> None:
        write_deltalake(
            self.table_path,
            data=pd.concat([pd.DataFrame([self._schema_record()]), records], ignore_index=True),
            mode="overwrite",
        )

    @staticmethod
    def _schema_record() -> dict[str, int | str]:
        return {"authorization_id": "__schema__", "expires_at": 0}