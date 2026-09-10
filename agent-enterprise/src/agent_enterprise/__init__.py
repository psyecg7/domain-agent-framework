"""Optional asymmetric authorization boundary for separately deployed services."""

from .authorization import (
    AuthorizationError,
    AuthorizedCommandExecutor,
    ExecutionCommand,
    InMemoryReplayStore,
    PolicyAuthorizationIssuer,
    SignedAuthorization,
    ExecutorAuthorizationVerifier,
)
from .delta_replay import DeltaReplayStore
from .http_reference import executor_server, policy_server
from .audit import DeltaAuthorizationAuditStore
from .inventory_reference import DeltaInventoryReservationHandler, InventorySnapshot, InventoryUnavailable
from .postgres_inventory_reference import PostgresInventoryReservationHandler

__all__ = [
    "AuthorizationError",
    "AuthorizedCommandExecutor",
    "ExecutionCommand",
    "ExecutorAuthorizationVerifier",
    "DeltaReplayStore",
    "executor_server",
    "policy_server",
    "DeltaAuthorizationAuditStore",
    "InMemoryReplayStore",
    "DeltaInventoryReservationHandler",
    "InventorySnapshot",
    "InventoryUnavailable",
    "PostgresInventoryReservationHandler",
    "PolicyAuthorizationIssuer",
    "SignedAuthorization",
]
