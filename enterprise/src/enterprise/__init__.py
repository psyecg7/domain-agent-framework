"""Optional asymmetric authorization boundary for separately deployed services."""

from .authorization import (
    AuthorizationError,
    AuthorizedCommandExecutor,
    ExecutionCommand,
    InMemoryReplayStore,
    InMemoryRevocationStore,
    ReplayStore,
    RevocationStore,
    PolicyAuthorizationIssuer,
    SignedAuthorization,
    ExecutorAuthorizationVerifier,
)
from .delta_replay import DeltaReplayStore
from .http_reference import executor_server, policy_server
from .audit import DeltaAuthorizationAuditStore
from .inventory_reference import DeltaInventoryReservationHandler, InventorySnapshot, InventoryUnavailable
from .postgres_inventory_reference import PostgresInventoryReservationHandler
from .postgres_authorization import AuthorizationStoreUnavailable, PostgresReplayStore, PostgresRevocationStore
from .oidc import AuthenticatedPrincipal, OidcJwtValidator, OidcValidationError
from .mtls import MtlsIdentityError, client_context, server_context, verify_peer_identity
from .vault_transit import VaultTransitPolicyAuthorizationIssuer, VaultTransitUnavailable
from .migrations import migrate_enterprise_authorization

__all__ = [
    "AuthorizationError",
    "AuthorizedCommandExecutor",
    "ExecutionCommand",
    "ExecutorAuthorizationVerifier",
    "DeltaReplayStore",
    "PostgresReplayStore",
    "PostgresRevocationStore",
    "AuthorizationStoreUnavailable",
    "AuthenticatedPrincipal",
    "OidcJwtValidator",
    "OidcValidationError",
    "MtlsIdentityError",
    "client_context",
    "server_context",
    "verify_peer_identity",
    "VaultTransitPolicyAuthorizationIssuer",
    "VaultTransitUnavailable",
    "migrate_enterprise_authorization",
    "executor_server",
    "policy_server",
    "DeltaAuthorizationAuditStore",
    "InMemoryReplayStore",
    "InMemoryRevocationStore",
    "ReplayStore",
    "RevocationStore",
    "DeltaInventoryReservationHandler",
    "InventorySnapshot",
    "InventoryUnavailable",
    "PostgresInventoryReservationHandler",
    "PolicyAuthorizationIssuer",
    "SignedAuthorization",
]
