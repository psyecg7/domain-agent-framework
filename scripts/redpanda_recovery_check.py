#!/usr/bin/env python3
"""Deliberately restart local Redpanda and verify uncommitted 2S redelivery.

This is a manual chaos check, not part of the normal pytest suite: it restarts
the local Compose service. Run only with REDPANDA_CHAOS_TESTS=1.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

if os.getenv("REDPANDA_CHAOS_TESTS") != "1":
    raise SystemExit("set REDPANDA_CHAOS_TESTS=1 to allow a local broker restart")

BOOTSTRAP = os.getenv("REDPANDA_BOOTSTRAP_SERVERS", "localhost:19092")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_core import Event
from agent_redpanda import RedpandaConsumer, RedpandaEventDispatcher, RedpandaEventTransport, RedpandaProducer

fixture_spec = importlib.util.spec_from_file_location(
    "_recovery_order_process", ROOT / "tests" / "test_pharmacy_reconciliation.py"
)
if fixture_spec is None or fixture_spec.loader is None:
    raise RuntimeError("unable to load the frozen 2S process fixture")
fixture = importlib.util.module_from_spec(fixture_spec)
sys.modules[fixture_spec.name] = fixture
fixture_spec.loader.exec_module(fixture)
OrderProcess = fixture.OrderProcess


def consumer(topic: str, group_id: str) -> RedpandaConsumer:
    return RedpandaConsumer(
        {"bootstrap.servers": BOOTSTRAP, "group.id": group_id, "auto.offset.reset": "earliest"}, topics=[topic]
    )


def dispatch_until(dispatcher: RedpandaEventDispatcher, predicate, *, tolerate_broker_errors: bool = False) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            dispatcher.dispatch(timeout=1.0)
        except RuntimeError:
            if not tolerate_broker_errors:
                raise
            time.sleep(0.5)
            continue
        if predicate():
            return
    raise RuntimeError("timed out waiting for broker recovery/redelivery")


prefix = f"agent-2s-recovery-{uuid.uuid4().hex}"
topic = f"{prefix}-results"
group_id = f"{prefix}-order"
event = Event(
    "inventory.reservation.reconciled",
    "process-1",
    "order",
    payload={
        "operation_id": "RES-RECOVERY",
        "attempt_id": "ATTEMPT-1",
        "reconciliation_id": "REC-RECOVERY",
        "reconciliation_sequence": 1,
        "reservation_status": "EXISTS",
    },
    source="inventory",
    metadata={"correlation_id": "C-RECOVERY", "causation_id": "recovery-request"},
)
transport = RedpandaEventTransport(RedpandaProducer({"bootstrap.servers": BOOTSTRAP}), topic_for_event=lambda _: topic)
transport.publish(event)
process = OrderProcess("process-1", "C-RECOVERY", "RES-RECOVERY", "ATTEMPT-1")

first = consumer(topic, group_id)
first_dispatcher = RedpandaEventDispatcher(first)
first_dispatcher.subscribe(event.event_type, process.observe)

def crash_after_process_observation(_: Event) -> None:
    raise RuntimeError("simulated process crash before offset acknowledgement")

first_dispatcher.subscribe(event.event_type, crash_after_process_observation)
try:
    dispatch_until(first_dispatcher, lambda: False)
except RuntimeError as exc:
    if "simulated process crash" not in str(exc):
        raise
finally:
    first.close()

subprocess.run(
    ["docker", "compose", "-f", str(ROOT / "docker-compose.redpanda.yml"), "restart", "redpanda"],
    cwd=ROOT,
    check=True,
)

redelivered: list[Event] = []
restarted = consumer(topic, group_id)
restarted_dispatcher = RedpandaEventDispatcher(restarted)

def observe_redelivery(delivered: Event) -> None:
    redelivered.append(delivered)
    process.observe(delivered)

restarted_dispatcher.subscribe(event.event_type, observe_redelivery)
try:
    dispatch_until(restarted_dispatcher, lambda: len(redelivered) == 1, tolerate_broker_errors=True)
finally:
    restarted.close()

assert redelivered[0].event_id == event.event_id
assert process.applied_event_ids == {event.event_id}
assert process.reconciliation_results == {"REC-RECOVERY": "EXISTS"}
print("PASS: broker restart preserved the uncommitted event; Order applied it once.")
