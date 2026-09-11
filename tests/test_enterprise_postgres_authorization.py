"""Portable checks for the shared authorization-store adapters.

SQLite exercises the API and restart behavior here.  The opt-in PostgreSQL
test proves that the replay primary key arbitrates concurrent workers.
"""

from __future__ import annotations

from agent_enterprise import PostgresReplayStore, PostgresRevocationStore


def test_sql_authorization_stores_survive_restart_and_expire_records(tmp_path) -> None:
    url = f"sqlite:///{tmp_path / 'authorization.db'}"
    replay = PostgresReplayStore(url)
    assert replay.claim("AUTH-1", 2_000_000_000) is True
    assert PostgresReplayStore(url).claim("AUTH-1", 2_000_000_000) is False
    assert replay.claim("EXPIRED", 1) is False

    revocations = PostgresRevocationStore(url)
    revocations.revoke("DEC-1", expires_at=2_000_000_000)
    assert PostgresRevocationStore(url).is_revoked("DEC-1", now=1_900_000_000) is True
    assert revocations.is_revoked("DEC-1", now=2_000_000_001) is False
    assert revocations.prune_expired(now=2_000_000_001) == 1
