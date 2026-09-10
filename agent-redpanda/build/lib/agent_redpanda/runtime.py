from __future__ import annotations

from typing import Any, Callable

from agent_core import Action, Agent, AgentResult, Event

from .mapper import RedpandaEventMapper
from .producer import RedpandaProducer


class RedpandaAgentRuntime:
    def __init__(
        self,
        agent: Agent,
        *,
        mapper: RedpandaEventMapper | None = None,
        producer: RedpandaProducer | None = None,
        default_topic: str = "agent.events",
        decision_topic: str = "agent.decisions",
        action_topic: str = "agent.actions",
        action_factory: Callable[[Any], Action] | None = None,
        retry_topic: str | None = None,
        dead_letter_topic: str | None = None,
        max_retries: int = 3,
    ) -> None:
        self.agent = agent
        self.mapper = mapper or RedpandaEventMapper()
        self.producer = producer
        self.default_topic = default_topic
        self.decision_topic = decision_topic
        self.action_topic = action_topic
        self.action_factory = action_factory
        self.retry_topic = retry_topic
        self.dead_letter_topic = dead_letter_topic
        self.max_retries = max_retries

    def process_record(self, record: dict[str, Any]) -> AgentResult:
        try:
            event = self.mapper.from_record(record)
            result = self.agent.process(event)
            if self.producer is not None:
                self._publish_result(result)
            return result
        except (TypeError, ValueError) as exc:
            self._route_failure(record, exc, terminal=True)
            raise
        except Exception as exc:
            self._route_failure(record, exc, terminal=False)
            raise

    def _route_failure(self, record: dict[str, Any], error: Exception, *, terminal: bool) -> None:
        if self.producer is None:
            return
        transport = record.get("_transport", {}) if isinstance(record, dict) else {}
        retry_count = int(transport.get("retry_count", 0)) if isinstance(transport, dict) else 0
        topic = self.dead_letter_topic if terminal or retry_count >= self.max_retries else self.retry_topic
        if not topic:
            return
        key = transport.get("key") if isinstance(transport, dict) else None
        source = {
            "topic": transport.get("topic") if isinstance(transport, dict) else None,
            "partition": transport.get("partition") if isinstance(transport, dict) else None,
            "offset": transport.get("offset") if isinstance(transport, dict) else None,
        }
        value = dict(record) if isinstance(record, dict) else {"raw_record": repr(record)}
        value["_failure"] = {
            "type": type(error).__name__,
            "message": str(error),
            "terminal": terminal,
            "retry_count": retry_count,
            "source": source,
        }
        self.producer.publish(topic=topic, key=key, value=value)

    def _publish_result(self, result: AgentResult) -> None:
        if self.action_factory is not None:
            for decision in result.decisions:
                self.producer.publish(
                    topic=self.decision_topic,
                    key=decision.entity_id,
                    value={
                        "event_type": "decision",
                        "entity_id": decision.entity_id,
                        "entity_type": decision.entity_type,
                        "decision_id": decision.decision_id,
                        "decision_type": decision.decision_type,
                        "severity": decision.severity,
                        "reason": decision.reason,
                        "created_at": decision.created_at.isoformat(),
                        "metadata": self._lineage_metadata(result),
                    },
                )

            for action in result.actions:
                action_value = self.action_factory(action)
                self.producer.publish(
                    topic=self.action_topic,
                    key=action.entity_id,
                    value={
                        "event_type": "action",
                        "entity_id": action.entity_id,
                        "entity_type": action.entity_type,
                        "action_id": action.action_id,
                        "action_type": action.action_type,
                        "parameters": action.parameters,
                        "created_at": action.created_at.isoformat(),
                        "metadata": self._lineage_metadata(result, action),
                    },
                )
            return

        for decision in result.decisions:
            self.producer.publish(
                topic=self.decision_topic,
                key=decision.entity_id,
                value={
                    "event_type": "decision",
                    "entity_id": decision.entity_id,
                    "entity_type": decision.entity_type,
                    "decision_id": decision.decision_id,
                    "decision_type": decision.decision_type,
                    "severity": decision.severity,
                    "reason": decision.reason,
                    "created_at": decision.created_at.isoformat(),
                    "metadata": self._lineage_metadata(result),
                },
            )
        for action in result.actions:
            self.producer.publish(
                topic=self.action_topic,
                key=action.entity_id,
                value={
                    "event_type": "action",
                    "entity_id": action.entity_id,
                    "entity_type": action.entity_type,
                    "action_id": action.action_id,
                    "action_type": action.action_type,
                    "parameters": action.parameters,
                    "created_at": action.created_at.isoformat(),
                    "metadata": self._lineage_metadata(result, action),
                },
            )

    def _lineage_metadata(self, result: AgentResult, action: Action | None = None) -> dict[str, str]:
        metadata = dict(result.event.metadata)
        if action is not None:
            metadata.update(action.metadata)
        lineage = {}
        for key in ("correlation_id", "causation_id", "traceparent", "tracestate"):
            value = metadata.get(key)
            if isinstance(value, str) and value:
                lineage[key] = value
        return lineage

    def process_event(self, event: Event) -> AgentResult:
        result = self.agent.process(event)
        if self.producer is not None:
            self._publish_result(result)
        return result
