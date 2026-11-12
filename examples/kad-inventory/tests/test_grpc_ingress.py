"""Executable proof that KAD command acceptance is streaming, not REST RPC."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import grpc
import pytest

from kad_inventory.command_inbox import InMemoryInventoryCommandInbox
from kad_inventory import OrderInventoryCoordinator, SqliteOrderProcessStore
from kad_inventory.contracts import MoveOrderIntent
from kad_inventory.grpc_ingress import GrpcOrderCommandDispatcher, InventoryCommandGateway
from kad_inventory.proto import inventory_command_v1_pb2 as pb
from kad_inventory.proto import inventory_command_v1_pb2_grpc as pb_grpc


def command(number: int, *, version: int = 1) -> pb.InventoryCommand:
    return pb.InventoryCommand(
        protocol_version=version,
        event_id=f"event-{number}",
        correlation_id=f"order:ORD-{number}",
        causation_id=f"order-submitted:ORD-{number}",
        operation_id=f"ORD-{number}:inventory-reallocation",
        attempt_id=f"ORD-{number}:inventory-reallocation:attempt-1",
        order_id=f"ORD-{number}",
        sku=f"SKU-{number}",
        target_warehouse="WH-A",
        quantity=1,
        expected_state_version=1,
    )


def start(server: grpc.Server) -> int:
    try:
        port = server.add_insecure_port("127.0.0.1:0")
    except RuntimeError:
        pytest.skip("environment forbids loopback gRPC listener binding")
    server.start()
    return port


def test_one_bidirectional_stream_accepts_many_commands_without_waiting_for_results() -> None:
    inbox = InMemoryInventoryCommandInbox()
    server = grpc.server(ThreadPoolExecutor(max_workers=4))
    pb_grpc.add_InventoryCommandGatewayServicer_to_server(InventoryCommandGateway(inbox), server)
    port = start(server)
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            acknowledgements = list(pb_grpc.InventoryCommandGatewayStub(channel).Submit(
                iter([*(command(number) for number in range(200)), command(0)]),
            ))
    finally:
        server.stop(grace=1).wait()

    assert [ack.status for ack in acknowledgements[:-1]] == [pb.CommandAcknowledgement.ACCEPTED] * 200
    assert acknowledgements[-1].status == pb.CommandAcknowledgement.DUPLICATE
    assert len(inbox.accepted()) == 200


def test_stream_rejects_one_bad_frame_without_dropping_other_commands() -> None:
    inbox = InMemoryInventoryCommandInbox()
    server = grpc.server(ThreadPoolExecutor(max_workers=2))
    pb_grpc.add_InventoryCommandGatewayServicer_to_server(InventoryCommandGateway(inbox), server)
    port = start(server)
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            acknowledgements = list(pb_grpc.InventoryCommandGatewayStub(channel).Submit(
                iter([command(1), command(2, version=99), command(3)]),
            ))
    finally:
        server.stop(grace=1).wait()

    assert [ack.status for ack in acknowledgements] == [
        pb.CommandAcknowledgement.ACCEPTED,
        pb.CommandAcknowledgement.REJECTED,
        pb.CommandAcknowledgement.ACCEPTED,
    ]
    assert len(inbox.accepted()) == 2


def test_order_outbox_is_marked_only_after_grpc_acceptance(tmp_path) -> None:
    inbox = InMemoryInventoryCommandInbox()
    server = grpc.server(ThreadPoolExecutor(max_workers=2))
    pb_grpc.add_InventoryCommandGatewayServicer_to_server(InventoryCommandGateway(inbox), server)
    port = start(server)
    try:
        with grpc.insecure_channel(f"127.0.0.1:{port}") as channel:
            store = SqliteOrderProcessStore(tmp_path / "orders.sqlite")
            # The empty publisher is unused: this path selects gRPC instead.
            coordinator = OrderInventoryCoordinator(store, publisher=object())
            correlation_ids = coordinator.persist_many([
                MoveOrderIntent(order_id=f"ORD-GRPC-{number}", sku=f"SKU-{number}", target_warehouse="WH-A", quantity=1, expected_state_version=1)
                for number in range(200)
            ])
            assert all(store.load(correlation_id).status == "PENDING_INVENTORY" for correlation_id in correlation_ids)

            dispatcher = GrpcOrderCommandDispatcher(store, pb_grpc.InventoryCommandGatewayStub(channel))
            assert dispatcher.dispatch_pending(limit=200) == 200
            assert len(inbox.accepted()) == 200
            assert store.claim_pending("another-worker", limit=200) == []
    finally:
        server.stop(grace=1).wait()
