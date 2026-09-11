"""PostgreSQL-backed authorization replay and revocation evidence.

These adapters are intentionally enterprise-layer infrastructure.  A primary
key is the cross-process arbiter: two Executor replicas cannot both claim one
authorization ID, and a revocation written by Policy is visible to every
Executor using the same table.  They do not replace service identity, key
custody, or a deployment's revocation-propagation SLO.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, MetaData, String, Table, and_, create_engine, delete, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError


class AuthorizationStoreUnavailable(RuntimeError):
    """The shared replay or revocation store could not be reached."""


def _table_name(value: str) -> str:
    if not isinstance(value, str) or not value.isidentifier():
        raise ValueError("table_name must be a SQL identifier")
    return value


def _identity(value: str, *, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


class PostgresReplayStore:
    """A shared atomic replay claim store for multiple Executor processes.

    ``claim`` returns true for exactly one committed claimant per authorization
    ID. Expiry pruning is deliberately outside the execution hot path; call
    ``prune_expired`` from one scheduled maintenance worker.
    """

    def __init__(self, database_url: str, *, table_name: str = "agent_authorization_replay") -> None:
        name = _table_name(table_name)
        try:
            self.engine: Engine = create_engine(database_url)
            metadata = MetaData()
            self.claims = Table(
                name,
                metadata,
                Column("authorization_id", String(255), primary_key=True),
                Column("expires_at", DateTime(timezone=True), nullable=False),
            )
            metadata.create_all(self.engine)
        except OperationalError as exc:
            raise AuthorizationStoreUnavailable("Authorization replay storage is unavailable") from exc

    def claim(self, authorization_id: str, expires_at: int) -> bool:
        _identity(authorization_id, name="authorization_id")
        if not isinstance(expires_at, int):
            raise TypeError("expires_at must be an integer epoch timestamp")
        now = datetime.now(timezone.utc)
        expiry = datetime.fromtimestamp(expires_at, timezone.utc)
        if expiry <= now:
            return False
        try:
            with self.engine.begin() as connection:
                connection.execute(insert(self.claims).values(authorization_id=authorization_id, expires_at=expiry))
                return True
        except IntegrityError:
            # The only uniqueness constraint on this table is authorization_id;
            # this is a replay, not an unrelated domain constraint.
            return False
        except OperationalError as exc:
            raise AuthorizationStoreUnavailable("Authorization replay storage is unavailable") from exc

    def prune_expired(self, *, now: int | None = None) -> int:
        current = datetime.fromtimestamp(now, timezone.utc) if now is not None else datetime.now(timezone.utc)
        try:
            with self.engine.begin() as connection:
                return int(connection.execute(delete(self.claims).where(self.claims.c.expires_at < current)).rowcount)
        except OperationalError as exc:
            raise AuthorizationStoreUnavailable("Authorization replay storage is unavailable") from exc


class PostgresRevocationStore:
    """A shared, durable decision-revocation set.

    Policy writes the decision ID and its original authorization expiry.  An
    Executor checks this set after signature and binding verification but
    before it claims the authorization for execution.  Revocation is therefore
    fail-closed once the write is visible; propagation latency remains a
    deployment SLO, not a property of a Python protocol.
    """

    def __init__(self, database_url: str, *, table_name: str = "agent_authorization_revocations") -> None:
        name = _table_name(table_name)
        try:
            self.engine: Engine = create_engine(database_url)
            metadata = MetaData()
            self.revocations = Table(
                name,
                metadata,
                Column("decision_id", String(255), primary_key=True),
                Column("expires_at", DateTime(timezone=True), nullable=False),
            )
            metadata.create_all(self.engine)
        except OperationalError as exc:
            raise AuthorizationStoreUnavailable("Authorization revocation storage is unavailable") from exc

    def revoke(self, decision_id: str, *, expires_at: int) -> None:
        _identity(decision_id, name="decision_id")
        if not isinstance(expires_at, int):
            raise TypeError("expires_at must be an integer epoch timestamp")
        expiry = datetime.fromtimestamp(expires_at, timezone.utc)
        try:
            with self.engine.begin() as connection:
                updated = connection.execute(
                    update(self.revocations)
                    .where(self.revocations.c.decision_id == decision_id)
                    .values(expires_at=expiry)
                )
                if updated.rowcount == 0:
                    connection.execute(insert(self.revocations).values(decision_id=decision_id, expires_at=expiry))
        except IntegrityError:
            # A concurrent writer inserted the same decision.  Its revocation
            # is already sufficient; never turn this into authorization.
            return
        except OperationalError as exc:
            raise AuthorizationStoreUnavailable("Authorization revocation storage is unavailable") from exc

    def is_revoked(self, decision_id: str, *, now: int) -> bool:
        _identity(decision_id, name="decision_id")
        if not isinstance(now, int):
            raise TypeError("now must be an integer epoch timestamp")
        current = datetime.fromtimestamp(now, timezone.utc)
        try:
            with self.engine.connect() as connection:
                return connection.execute(
                    select(self.revocations.c.decision_id).where(
                        and_(
                            self.revocations.c.decision_id == decision_id,
                            self.revocations.c.expires_at >= current,
                        )
                    )
                ).first() is not None
        except OperationalError as exc:
            raise AuthorizationStoreUnavailable("Authorization revocation storage is unavailable") from exc

    def prune_expired(self, *, now: int | None = None) -> int:
        current = datetime.fromtimestamp(now, timezone.utc) if now is not None else datetime.now(timezone.utc)
        try:
            with self.engine.begin() as connection:
                return int(connection.execute(delete(self.revocations).where(self.revocations.c.expires_at < current)).rowcount)
        except OperationalError as exc:
            raise AuthorizationStoreUnavailable("Authorization revocation storage is unavailable") from exc
