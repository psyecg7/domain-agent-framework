"""Transactional receipt composition for state-only raw ``Agent`` processing.

The runner intentionally supports only an Agent with no action executor. It
can atomically persist an inbound event receipt and the Agent's PostgreSQL state
observation, but it cannot include external action execution in that database
transaction. Actions returned by the Agent remain plans for a separate,
domain-owned outbox or execution boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_core import Agent, AgentResult, Event

from .receipts import EventReceiptResult, PostgresEventReceiptStore
from .store import PostgresStateStore


@dataclass(frozen=True)
class AgentReceiptResult:
    """The receipt outcome and in-process Agent result, if newly applied."""

    receipt: EventReceiptResult
    result: AgentResult | None


class PostgresAgentReceiptRunner:
    """Apply one Event to one state-only Agent in a shared PostgreSQL transaction.

    The caller commits the transport offset only after ``process_once``
    returns. On a duplicate event, ``result`` is ``None`` because no fresh
    policy evaluation occurred; the persisted domain state is authoritative.
    """

    def __init__(
        self,
        agent: Agent,
        *,
        state_store: PostgresStateStore,
        receipt_store: PostgresEventReceiptStore,
    ) -> None:
        if not isinstance(agent, Agent):
            raise TypeError("agent must be an Agent")
        if agent.state_store is not state_store:
            raise ValueError("agent must use the supplied PostgresStateStore")
        if agent.action_executor is not None:
            raise ValueError("PostgresAgentReceiptRunner requires an Agent without an action executor")
        if state_store.engine.url != receipt_store.engine.url:
            raise ValueError("state_store and receipt_store must use the same database URL")
        self.agent = agent
        self.state_store = state_store
        self.receipt_store = receipt_store

    def process_once(self, event: Event) -> AgentReceiptResult:
        """Persist one state observation and event receipt, or skip a duplicate."""
        if not isinstance(event, Event):
            raise TypeError("event must be an Event")
        applied_result: AgentResult | None = None

        def apply(connection) -> None:
            nonlocal applied_result
            with self.state_store.use_connection(connection):
                applied_result = self.agent.process(event)

        receipt = self.receipt_store.apply_once(event.event_id, apply=apply)
        return AgentReceiptResult(receipt=receipt, result=applied_result)


__all__ = ["AgentReceiptResult", "PostgresAgentReceiptRunner"]
