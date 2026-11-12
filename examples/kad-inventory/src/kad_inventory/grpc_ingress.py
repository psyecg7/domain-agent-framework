"""gRPC streaming ingress for asynchronous Inventory commands."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Protocol

from .command_inbox import InventoryCommandInbox
from .contracts import KafkaMessage, MessageMetadata, MoveOrderIntent
from .proto import inventory_command_v1_pb2 as pb
from .proto import inventory_command_v1_pb2_grpc as pb_grpc


PROTOCOL_VERSION = 1


class PendingOrderCommandStore(Protocol):
    """The Order outbox operations needed by the gRPC command dispatcher."""

    def claim_pending(self, worker_id: str, *, limit: int = 100, lease_seconds: int = 30) -> list[KafkaMessage]: ...

    def mark_published(self, event_id: str, *, worker_id: str) -> None: ...

    def release_pending(self, event_id: str, *, worker_id: str) -> None: ...


def command_from_wire(request: pb.InventoryCommand) -> KafkaMessage:
    """Decode one v1 frame into the existing domain command envelope."""
    if request.protocol_version != PROTOCOL_VERSION:
        raise ValueError(f"unsupported Inventory command protocol version: {request.protocol_version}")
    intent = MoveOrderIntent(
        order_id=request.order_id,
        sku=request.sku,
        target_warehouse=request.target_warehouse,
        quantity=request.quantity,
        expected_state_version=request.expected_state_version,
    )
    return KafkaMessage(
        event_id=request.event_id,
        event_type="InventoryReallocationRequested",
        entity_id=request.order_id,
        payload=intent.model_dump(),
        metadata=MessageMetadata(
            correlation_id=request.correlation_id,
            causation_id=request.causation_id,
            operation_id=request.operation_id,
            attempt_id=request.attempt_id,
        ),
    )


def acknowledgement_for(request: pb.InventoryCommand, *, status: int, detail: str = "") -> pb.CommandAcknowledgement:
    return pb.CommandAcknowledgement(
        protocol_version=PROTOCOL_VERSION,
        event_id=request.event_id,
        operation_id=request.operation_id,
        attempt_id=request.attempt_id,
        status=status,
        detail=detail,
    )


class InventoryCommandGateway(pb_grpc.InventoryCommandGatewayServicer):
    """Acknowledges acceptance after the domain inbox has recorded a command.

    The stream carries many independent commands concurrently in flight. An
    acknowledgement says only that Inventory durably accepted the command;
    execution outcomes stay asynchronous domain result events.
    """

    def __init__(self, inbox: InventoryCommandInbox) -> None:
        self.inbox = inbox

    def Submit(
        self, request_iterator: Iterable[pb.InventoryCommand], _context: object,
    ) -> Iterator[pb.CommandAcknowledgement]:
        for request in request_iterator:
            try:
                acceptance = self.inbox.accept(command_from_wire(request))
            except ValueError as exc:
                yield acknowledgement_for(
                    request, status=pb.CommandAcknowledgement.REJECTED, detail=str(exc),
                )
                continue
            status = pb.CommandAcknowledgement.ACCEPTED if acceptance == "ACCEPTED" else pb.CommandAcknowledgement.DUPLICATE
            yield acknowledgement_for(request, status=status)


class GrpcOrderCommandDispatcher:
    """Sends a leased Order outbox batch through one gRPC stream.

    A broker publication mark is reused here to mean *the selected command
    transport durably accepted the record*. This component deliberately knows
    nothing about the later Inventory result; Order observes that through its
    result-event path.
    """

    def __init__(self, store: PendingOrderCommandStore, stub: pb_grpc.InventoryCommandGatewayStub) -> None:
        self.store = store
        self.stub = stub

    def dispatch_pending(self, *, worker_id: str = "kad-order-grpc", limit: int = 100) -> int:
        pending = self.store.claim_pending(worker_id, limit=limit)
        if not pending:
            return 0
        pending_by_event = {command.event_id: command for command in pending}
        acknowledged: set[str] = set()
        try:
            for receipt in self.stub.Submit(command_to_wire(command) for command in pending):
                command = pending_by_event.get(receipt.event_id)
                if command is None or receipt.event_id in acknowledged:
                    raise RuntimeError("gRPC command gateway returned an unknown or duplicate acknowledgement")
                if receipt.operation_id != command.metadata.operation_id or receipt.attempt_id != command.metadata.attempt_id:
                    raise RuntimeError("gRPC command acknowledgement has mismatched operation lineage")
                if receipt.status in (pb.CommandAcknowledgement.ACCEPTED, pb.CommandAcknowledgement.DUPLICATE):
                    self.store.mark_published(command.event_id, worker_id=worker_id)
                    acknowledged.add(command.event_id)
                    continue
                if receipt.status == pb.CommandAcknowledgement.REJECTED:
                    self.store.release_pending(command.event_id, worker_id=worker_id)
                    acknowledged.add(command.event_id)
                    raise ValueError(f"Inventory rejected command {command.metadata.operation_id}: {receipt.detail}")
                raise RuntimeError("gRPC command gateway returned an unspecified acknowledgement")
            missing = set(pending_by_event).difference(acknowledged)
            if missing:
                raise RuntimeError("gRPC command gateway ended without acknowledging every command")
            return len(pending)
        except Exception:
            for event_id, command in pending_by_event.items():
                if event_id not in acknowledged:
                    self.store.release_pending(command.event_id, worker_id=worker_id)
            raise


def command_to_wire(command: KafkaMessage) -> pb.InventoryCommand:
    """Encode a persisted Order command for a gRPC client stream."""
    intent = MoveOrderIntent.model_validate(command.payload)
    return pb.InventoryCommand(
        protocol_version=PROTOCOL_VERSION,
        event_id=command.event_id,
        correlation_id=command.metadata.correlation_id,
        causation_id=command.metadata.causation_id,
        operation_id=command.metadata.operation_id,
        attempt_id=command.metadata.attempt_id,
        order_id=intent.order_id,
        sku=intent.sku,
        target_warehouse=intent.target_warehouse,
        quantity=intent.quantity,
        expected_state_version=intent.expected_state_version,
    )
