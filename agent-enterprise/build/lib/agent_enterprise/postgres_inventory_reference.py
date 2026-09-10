"""Enterprise command adapter for the transactional Inventory authority."""

from __future__ import annotations

from agent_postgres import PostgresInventoryReservationAuthority

from .authorization import ExecutionCommand


class PostgresInventoryReservationHandler:
    """Translate a verified, signed reservation command into an Inventory effect."""

    def __init__(self, authority: PostgresInventoryReservationAuthority) -> None:
        self.authority = authority

    def preconditions_for(self, product_id: str) -> dict[str, int]:
        return self.authority.preconditions_for(product_id)

    def __call__(self, command: ExecutionCommand) -> str:
        if command.action_type != "RESERVE" or command.entity_type != "inventory":
            raise ValueError("Inventory only accepts RESERVE commands for inventory entities")
        quantity = command.parameters.get("quantity")
        if not isinstance(quantity, int):
            raise ValueError("Inventory reservation quantity must be an integer")
        return self.authority.reserve(
            command.action_id,
            command.entity_id,
            quantity,
            preconditions=command.preconditions,
        )
