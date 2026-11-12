"""Runnable, domain-owned reference for knowledge-aware Inventory.

This package is an example application.  It does not add a semantic, OCC, or
Kafka primitive to ``agent-core``.
"""

from .service import create_app
from .state import InMemoryInventoryState, InvariantViolationError
from .order_protocol import (
    InventoryReallocationCommandHandler, OrderInventoryCoordinator,
    PostgresOrderProcessStore, SqliteOrderProcessStore,
)
from .events import KafkaInventoryCommandConsumer, KafkaOrderResultConsumer
from .command_inbox import PostgresInventoryCommandInbox

__all__ = [
    "InMemoryInventoryState", "InvariantViolationError", "create_app",
    "InventoryReallocationCommandHandler", "OrderInventoryCoordinator",
    "PostgresOrderProcessStore", "SqliteOrderProcessStore",
    "KafkaInventoryCommandConsumer", "KafkaOrderResultConsumer",
    "PostgresInventoryCommandInbox",
]
