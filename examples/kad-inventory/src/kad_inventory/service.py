"""FastAPI boundary for evaluation and guarded Inventory execution."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .contracts import (
    CommandResult, EvaluateRequest, EvaluationResult, GuardedCommand,
    InventoryReallocated, MoveOrderIntent,
)
from .events import EventPublisher
from .state import InMemoryInventoryState, InvariantViolationError


def compile_guarded_command(intent: MoveOrderIntent) -> GuardedCommand:
    """Policy compilation: retain the version proof supplied by the intent.

    Real Policy code must obtain or validate that proof from the authoritative
    evaluation path; it must never invent a current version at execution time.
    """
    return GuardedCommand(**intent.model_dump())


def create_app(state: InMemoryInventoryState, publisher: EventPublisher) -> FastAPI:
    app = FastAPI(title="Knowledge-aware Inventory reference")

    @app.post("/evaluate-alternative", response_model=EvaluationResult)
    async def evaluate_alternative(request: EvaluateRequest) -> EvaluationResult:
        # Deliberately read-only: no event is published and no state mutates.
        return state.evaluate(
            sku=request.sku,
            warehouse=request.target_warehouse,
            quantity=request.quantity,
        )

    @app.post("/execute-intent", response_model=CommandResult)
    async def execute_intent(intent: MoveOrderIntent) -> CommandResult:
        command = compile_guarded_command(intent)
        try:
            result = state.execute_with_predicates(command)
        except InvariantViolationError as exc:
            publisher.publish(exc.failure.model_dump())
            raise HTTPException(status_code=409, detail=exc.failure.model_dump()) from exc
        publisher.publish(InventoryReallocated(
            order_id=command.order_id,
            sku=command.sku,
            target_warehouse=command.target_warehouse,
            quantity=command.quantity,
            state_version=result.state_version,
        ).model_dump())
        return result

    return app
