"""Verify the published event-envelope v1 vector against the broker mapper."""

from __future__ import annotations

import json
from pathlib import Path

from transport_redpanda import RedpandaEventMapper


VECTOR = Path(__file__).resolve().parents[2] / "protocol" / "test-vectors" / "events" / "event-envelope-v1.json"


def test_event_envelope_v1_vector_round_trips() -> None:
    record = json.loads(VECTOR.read_text())["record"]
    mapper = RedpandaEventMapper()

    event = mapper.from_record(record)

    assert mapper.to_record(event) == record
