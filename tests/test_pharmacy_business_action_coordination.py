from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable
import inspect

from agent_application import CapabilityInvoker
from agent_core import Action, Agent, Capability, Decision, Event, InMemoryCapabilityRegistry, Intent, State


class StateStore:
    def __init__(self) -> None:
        self.states: dict[tuple[str, str], State] = {}

    def get(self, entity_id: str, entity_type: str) -> State | None:
        return self.states.get((entity_id, entity_type))

    def save(self, state: State) -> None:
        self.states[(state.entity_id, state.entity_type)] = state


class DeferredTransport:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.pending: list[Event] = []
        self.handlers: dict[str, list[Callable[[Event], Any]]] = defaultdict(list)

    def subscribe(self, event_type: str, handler: Callable[[Event], Any]) -> None:
        self.handlers[event_type].append(handler)

    def publish(self, event: Event) -> None:
        self.events.append(event)
        self.pending.append(event)

    def deliver_next(self, event_type: str) -> Event:
        event = next(event for event in self.pending if event.event_type == event_type)
        self.pending.remove(event)
        for handler in self.handlers[event.event_type]:
            handler(event)
        return event

    def deliver_all(self, event_type: str) -> None:
        while any(event.event_type == event_type for event in self.pending):
            self.deliver_next(event_type)


class BusinessPolicy:
    def __init__(self, allowed: Callable[[State], bool], decision_type: str) -> None:
        self.allowed = allowed
        self.decision_type = decision_type
        self.calls = 0

    def evaluate(self, state: State) -> list[Decision]:
        self.calls += 1
        if not self.allowed(state):
            return []
        return [
            Decision(
                entity_id=state.entity_id,
                entity_type=state.entity_type,
                decision_type=self.decision_type,
                severity="LOW",
                reason="Local domain policy allowed the operation",
            )
        ]


class RecordingExecutor:
    def __init__(self, transport: DeferredTransport, result_type: str, result_factory: Callable[[Action], dict[str, Any]]) -> None:
        self.transport = transport
        self.result_type = result_type
        self.result_factory = result_factory
        self.actions: list[Action] = []

    def execute(self, action: Action) -> None:
        self.actions.append(action)
        self.transport.publish(
            Event(
                event_type=self.result_type,
                entity_id=action.entity_id,
                entity_type=action.entity_type,
                payload=self.result_factory(action),
                source="domain",
                metadata={
                    "correlation_id": action.metadata["correlation_id"],
                    "causation_id": action.action_id,
                },
            )
        )


class PurchaseCoordinator:
    """Test-local Order responsibility used to expose coordination semantics."""

    fact_capabilities = (
        "product.information.retrieve",
        "inventory.availability.check",
        "pricing.price.retrieve",
    )

    def __init__(self, invoker: CapabilityInvoker, transport: DeferredTransport) -> None:
        self.invoker = invoker
        self.transport = transport
        self.results: dict[str, dict[str, Event]] = defaultdict(dict)
        self.final_events: list[Event] = []
        self.side_effects_started: dict[str, bool] = defaultdict(bool)
        for result_type in (
            "pim.result",
            "inventory.availability.result",
            "pricing.result",
            "order.created.result",
            "inventory.reservation.result",
        ):
            transport.subscribe(result_type, self.receive)

    def start(self, order_id: str, product_id: str, correlation_id: str) -> None:
        for capability_id in self.fact_capabilities:
            self.invoker.invoke(
                Intent(capability_id, {"order_id": order_id, "product_id": product_id}),
                entity_id=product_id,
                entity_type="purchase_request",
                context={"correlation_id": correlation_id, "causation_id": "buy.request"},
            )

    def request_reservation_again(self, order_id: str, product_id: str, correlation_id: str) -> None:
        self._invoke_action(
            "inventory.reservation.create",
            {"order_id": order_id, "product_id": product_id, "quantity": 1, "idempotency_key": f"{order_id}:{product_id}:1"},
            correlation_id,
            "duplicate.request",
        )

    def receive(self, event: Event) -> None:
        correlation_id = event.metadata.get("correlation_id")
        if not isinstance(correlation_id, str):
            return
        if event.event_type in {"pim.result", "inventory.availability.result", "pricing.result"}:
            domain = {"pim.result": "pim", "inventory.availability.result": "inventory", "pricing.result": "pricing"}[event.event_type]
            self.results[correlation_id].setdefault(domain, event)
            if len(self.results[correlation_id]) == 3 and not self.side_effects_started[correlation_id]:
                self.side_effects_started[correlation_id] = True
                if all(result.payload.get("valid") is True for result in self.results[correlation_id].values()):
                    self._invoke_action("order.create", {"order_id": "ORD-1", "product_id": "P123"}, correlation_id, event.event_id)
                    self._invoke_action(
                        "inventory.reservation.create",
                        {"order_id": "ORD-1", "product_id": "P123", "quantity": 1, "idempotency_key": "ORD-1:P123:1"},
                        correlation_id,
                        event.event_id,
                    )
                else:
                    self._publish_final(correlation_id, False, event.event_id, "A required domain fact was denied")
            return

        if event.event_type in {"order.created.result", "inventory.reservation.result"}:
            self.results[correlation_id][event.event_type] = event
            action_results = {
                key: value
                for key, value in self.results[correlation_id].items()
                if key.endswith(".result") and key in {"order.created.result", "inventory.reservation.result"}
            }
            if len(action_results) == 2:
                success = all(result.payload.get("success") is True for result in action_results.values())
                self._publish_final(correlation_id, success, event.event_id, "All requested actions completed" if success else "A requested action failed")

    def _invoke_action(self, capability_id: str, parameters: dict[str, Any], correlation_id: str, causation_id: str) -> None:
        self.invoker.invoke(
            Intent(capability_id, parameters),
            entity_id=parameters["order_id"],
            entity_type="order",
            context={"correlation_id": correlation_id, "causation_id": causation_id},
        )

    def _publish_final(self, correlation_id: str, success: bool, causation_id: str, reason: str) -> None:
        if any(event.metadata.get("correlation_id") == correlation_id for event in self.final_events):
            return
        event = Event(
            event_type="purchase.result",
            entity_id="ORD-1",
            entity_type="order",
            payload={"success": success, "reason": reason},
            source="order",
            metadata={"correlation_id": correlation_id, "causation_id": causation_id},
        )
        self.final_events.append(event)
        self.transport.publish(event)


def capability(capability_id: str, owner: str) -> Capability:
    return Capability(
        capability_id=capability_id,
        name=capability_id,
        description=f"{owner} capability",
        metadata={"owner": owner, "intent_types": (capability_id,)},
    )


def build_application(
    *,
    product_valid: bool = True,
    inventory_available: bool = True,
    price_valid: bool = True,
    order_allowed: bool = True,
    reservation_allowed: bool = True,
) -> tuple[DeferredTransport, PurchaseCoordinator, dict[str, StateStore], dict[str, RecordingExecutor], InMemoryCapabilityRegistry]:
    registry = InMemoryCapabilityRegistry()
    capabilities = (
        ("product.information.retrieve", "pim"),
        ("inventory.availability.check", "inventory"),
        ("pricing.price.retrieve", "pricing"),
        ("order.create", "order"),
        ("inventory.reservation.create", "inventory"),
    )
    for capability_id, owner in capabilities:
        registry.register(capability(capability_id, owner))

    transport = DeferredTransport()
    stores = {domain: StateStore() for domain in ("pim", "inventory", "pricing", "order")}
    stores["pim"].save(State("P123", "pim", {"valid": product_valid}))
    stores["inventory"].save(State("P123", "inventory", {"available": inventory_available, "stock": 1}))
    stores["pricing"].save(State("P123", "pricing", {"valid": price_valid, "price": 12.50}))
    stores["order"].save(State("ORD-1", "order", {"allowed": order_allowed}))

    policies = {
        "pim": BusinessPolicy(lambda state: state.values.get("valid") is True, "PRODUCT_VALID"),
        "inventory": BusinessPolicy(lambda state: state.values.get("available") is True, "INVENTORY_AVAILABLE"),
        "pricing": BusinessPolicy(lambda state: state.values.get("valid") is True, "PRICE_VALID"),
        "order": BusinessPolicy(lambda state: state.values.get("allowed") is True, "ORDER_CREATED"),
        "reservation": BusinessPolicy(lambda state: reservation_allowed and state.values.get("available") is True, "RESERVATION_CREATED"),
    }
    executors = {
        "pim": RecordingExecutor(transport, "pim.result", lambda action: {"valid": product_valid, "action_id": action.action_id}),
        "inventory": RecordingExecutor(transport, "inventory.availability.result", lambda action: {"valid": inventory_available, "action_id": action.action_id}),
        "pricing": RecordingExecutor(transport, "pricing.result", lambda action: {"valid": price_valid, "action_id": action.action_id}),
        "order": RecordingExecutor(transport, "order.created.result", lambda action: {"success": order_allowed, "action_id": action.action_id}),
        "reservation": RecordingExecutor(transport, "inventory.reservation.result", lambda action: {"success": reservation_allowed, "action_id": action.action_id}),
    }
    agents = {
        "pim": Agent(stores["pim"], policies["pim"], action_executor=executors["pim"]),
        "inventory": Agent(stores["inventory"], policies["inventory"], action_executor=executors["inventory"]),
        "pricing": Agent(stores["pricing"], policies["pricing"], action_executor=executors["pricing"]),
        "order": Agent(stores["order"], policies["order"], action_executor=executors["order"]),
        "reservation": Agent(stores["inventory"], policies["reservation"], action_executor=executors["reservation"]),
    }

    def route(event: Event) -> None:
        capability_id = event.payload["capability_id"]
        routes = {
            "product.information.retrieve": ("pim", "pim"),
            "inventory.availability.check": ("inventory", "inventory"),
            "pricing.price.retrieve": ("pricing", "pricing"),
            "order.create": ("order", "order"),
            "inventory.reservation.create": ("reservation", "inventory"),
        }
        agent_name, entity_type = routes[capability_id]
        local_event = Event(
            event_type=event.event_type,
            entity_id="P123" if agent_name != "order" else "ORD-1",
            entity_type=entity_type,
            payload={"capability_id": capability_id, "request": event.payload["intent"]},
            source=event.source,
            metadata=dict(event.metadata),
        )
        result = agents[agent_name].process(local_event)
        if result.actions:
            return
        failure_types = {
            "product.information.retrieve": "pim.result",
            "inventory.availability.check": "inventory.availability.result",
            "pricing.price.retrieve": "pricing.result",
            "order.create": "order.created.result",
            "inventory.reservation.create": "inventory.reservation.result",
        }
        payload = {"valid": False} if capability_id.endswith(("retrieve", "check")) else {"success": False}
        transport.publish(
            Event(
                event_type=failure_types[capability_id],
                entity_id=local_event.entity_id,
                entity_type=local_event.entity_type,
                payload=payload,
                source=agent_name,
                metadata={"correlation_id": event.metadata["correlation_id"], "causation_id": local_event.event_id},
            )
        )

    transport.subscribe("capability.invocation", route)
    return transport, PurchaseCoordinator(CapabilityInvoker(registry, transport), transport), stores, executors, registry


def start_and_deliver_facts(transport: DeferredTransport, coordinator: PurchaseCoordinator, correlation_id: str = "buy-1") -> None:
    coordinator.start("ORD-1", "P123", correlation_id)
    transport.deliver_all("capability.invocation")


def test_buy_success_requires_validation_price_order_and_reservation() -> None:
    transport, coordinator, stores, executors, registry = build_application()
    start_and_deliver_facts(transport, coordinator)

    transport.deliver_next("pricing.result")
    transport.deliver_next("pim.result")
    transport.deliver_next("inventory.availability.result")
    assert [event.payload["capability_id"] for event in transport.pending if event.event_type == "capability.invocation"] == [
        "order.create",
        "inventory.reservation.create",
    ]

    transport.deliver_all("capability.invocation")
    transport.deliver_next("inventory.reservation.result")
    transport.deliver_next("order.created.result")

    assert coordinator.final_events[0].payload["success"] is True
    assert coordinator.final_events[0].metadata["correlation_id"] == "buy-1"
    assert executors["order"].actions and executors["reservation"].actions
    assert registry.get("inventory.reservation.create").metadata["owner"] == "inventory"
    assert stores["order"].get("ORD-1", "order") is not None


def test_policy_denial_prevents_side_effects() -> None:
    for overrides, denied_executor in (
        ({"product_valid": False}, "pim"),
        ({"inventory_available": False}, "inventory"),
        ({"price_valid": False}, "pricing"),
    ):
        transport, coordinator, _, executors, _ = build_application(**overrides)
        start_and_deliver_facts(transport, coordinator, f"deny-{denied_executor}")
        transport.deliver_all("pim.result")
        transport.deliver_all("inventory.availability.result")
        transport.deliver_all("pricing.result")
        assert executors[denied_executor].actions == []
        assert not any(event.metadata.get("capability_id") in {"order.create", "inventory.reservation.create"} for event in transport.events)
        assert coordinator.final_events[0].payload["success"] is False


def test_order_denial_prevents_order_action_but_inventory_reservation_is_not_automatic() -> None:
    transport, coordinator, _, executors, _ = build_application(order_allowed=False)
    start_and_deliver_facts(transport, coordinator)
    transport.deliver_all("pricing.result")
    transport.deliver_all("pim.result")
    transport.deliver_all("inventory.availability.result")
    transport.deliver_all("capability.invocation")
    transport.deliver_all("order.created.result")
    transport.deliver_all("inventory.reservation.result")

    assert executors["order"].actions == []
    assert len(executors["reservation"].actions) == 1
    assert coordinator.final_events[0].payload["success"] is False


def test_reservation_denial_after_order_creation_exposes_partial_completion() -> None:
    transport, coordinator, _, executors, _ = build_application(reservation_allowed=False)
    start_and_deliver_facts(transport, coordinator)
    transport.deliver_all("pricing.result")
    transport.deliver_all("pim.result")
    transport.deliver_all("inventory.availability.result")
    transport.deliver_all("capability.invocation")
    transport.deliver_all("inventory.reservation.result")
    transport.deliver_all("order.created.result")

    assert len(executors["order"].actions) == 1
    assert executors["reservation"].actions == []
    assert coordinator.final_events[0].payload["success"] is False
    assert coordinator.final_events[0].payload["reason"] == "A requested action failed"


def test_order_creation_failure_after_reservation_success_exposes_uncompensated_side_effect() -> None:
    transport, coordinator, _, executors, _ = build_application(order_allowed=False)
    coordinator.start("ORD-1", "P123", "partial-1")
    transport.deliver_all("capability.invocation")
    transport.deliver_all("pim.result")
    transport.deliver_all("inventory.availability.result")
    transport.deliver_all("pricing.result")
    transport.deliver_all("capability.invocation")
    transport.deliver_next("inventory.reservation.result")
    transport.deliver_next("order.created.result")

    assert len(executors["reservation"].actions) == 1
    assert executors["order"].actions == []
    assert coordinator.final_events[0].payload["success"] is False
    assert not any(event.metadata.get("capability_id") == "inventory.release" for event in transport.events)


def test_duplicate_reservation_requests_are_not_idempotent_automatically() -> None:
    transport, coordinator, _, executors, _ = build_application()
    start_and_deliver_facts(transport, coordinator)
    transport.deliver_all("capability.invocation")
    transport.deliver_all("pim.result")
    transport.deliver_all("inventory.availability.result")
    transport.deliver_all("pricing.result")
    transport.deliver_all("capability.invocation")
    transport.deliver_all("inventory.reservation.result")
    transport.deliver_all("order.created.result")

    coordinator.request_reservation_again("ORD-1", "P123", "buy-1")
    transport.deliver_all("capability.invocation")
    transport.deliver_all("inventory.reservation.result")

    assert len(executors["reservation"].actions) == 2
    assert executors["reservation"].actions[0].parameters.get("idempotency_key") is None
    assert executors["reservation"].actions[1].parameters.get("idempotency_key") is None


def test_late_result_remains_correlated_and_no_automatic_chaining_occurs() -> None:
    transport, coordinator, _, _, _ = build_application()
    start_and_deliver_facts(transport, coordinator)
    transport.deliver_next("pricing.result")
    transport.deliver_next("inventory.availability.result")
    assert coordinator.final_events == []
    transport.deliver_next("pim.result")
    transport.deliver_all("capability.invocation")
    transport.deliver_next("inventory.reservation.result")
    transport.deliver_next("order.created.result")

    assert coordinator.final_events[0].metadata["correlation_id"] == "buy-1"
    assert len([event for event in transport.events if event.event_type == "capability.invocation"]) == 5
    assert all(event.metadata.get("capability_id") != "inventory.release" for event in transport.events)


def test_coordination_boundary_has_no_direct_agent_or_shared_state_dependency() -> None:
    source = inspect.getsource(PurchaseCoordinator)
    assert "Agent" not in source
    assert ".process(" not in source
    assert "StateStore" not in source


def test_lineage_links_requests_actions_and_final_result() -> None:
    transport, coordinator, _, executors, _ = build_application()
    start_and_deliver_facts(transport, coordinator, "lineage-1")
    transport.deliver_all("pim.result")
    transport.deliver_all("inventory.availability.result")
    transport.deliver_all("pricing.result")
    fact_invocations = [event for event in transport.events if event.event_type == "capability.invocation"]
    assert all(event.metadata["correlation_id"] == "lineage-1" for event in fact_invocations)
    transport.deliver_all("capability.invocation")
    transport.deliver_all("inventory.reservation.result")
    transport.deliver_all("order.created.result")
    assert coordinator.final_events[0].metadata["correlation_id"] == "lineage-1"
    assert coordinator.final_events[0].metadata["causation_id"] in {
        event.event_id for event in transport.events if event.event_type.endswith(".result")
    }
    assert executors["reservation"].actions[0].metadata["correlation_id"] == "lineage-1"
