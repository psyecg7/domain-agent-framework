import time
import tempfile
from pathlib import Path

# Top-level primitives and runtime
from agent_core import (
    Agent, Event, Decision, State, Recommendation, Action, 
    decision_to_action
)

# Infrastructure ports
from agent_core.ports import PolicyEngine, StateStore

from agent_enterprise.audit import DeltaAuthorizationAuditStore

class InMemoryStateStore(StateStore):
    def __init__(self):
        self._states = {}
    def get(self, entity_id: str, entity_type: str) -> State | None:
        return self._states.get((entity_id, entity_type))
    def save(self, state: State) -> None:
        self._states[(state.entity_id, state.entity_type)] = state

class ProcurementPolicyEngine(PolicyEngine):
    """The deterministic firewall protecting the balance sheet."""
    def __init__(self, audit_store: DeltaAuthorizationAuditStore, max_qty: int = 50):
        self.audit_store = audit_store
        self.max_qty = max_qty

    def evaluate(self, state: State) -> list[Decision]:
        if state.values.get("stock", 0) < state.values.get("threshold", 10):
            return [Decision(state.entity_id, state.entity_type, "EVALUATE_REPLENISHMENT", "LOW", "Stock below threshold")]
        return []

    def evaluate_recommendations(self, state: State, recommendations: list[Recommendation]) -> list[Decision]:
        decisions = []
        for rec in recommendations:
            if rec.recommendation_type != "PROCURE_STOCK":
                continue
                
            qty = rec.parameters.get("quantity", 0)
            
            # 1. TOCTOU / Stale Context Check
            if rec.metadata.get("state_version") != state.version:
                decisions.append(Decision(state.entity_id, state.entity_type, "DENY", "HIGH", "Stale AI context"))
                continue
                
            # 2. The Deterministic Firewall
            if qty > self.max_qty:
                msg = f"AI proposed {qty} units. Hard limit is {self.max_qty}. Blocked."
                self.audit_store.record("POLICY_DENIED", principal_id="ai-reasoner-01", detail=msg)
                decisions.append(Decision(state.entity_id, state.entity_type, "DENY", "CRITICAL", msg))
                continue
                
            decisions.append(Decision(state.entity_id, state.entity_type, "APPROVE", "LOW", f"Approved {qty} units"))
        return decisions

class RunawayReasoner:
    """Simulates an LLM hallucinating a crisis and proposing a catastrophic order."""
    def reason(self, context) -> list[Recommendation]:
        return [Recommendation(
            "PROCURE_STOCK",
            rationale="CRITICAL: Global chip shortage detected in semantic memory. Securing 10,000 units immediately to prevent supply chain collapse.",
            parameters={"product_id": context.state.entity_id, "quantity": 10000},
            confidence=0.99,
            metadata={"state_version": context.state.version}
        )]

class NullExecutor:
    def execute(self, action: Action) -> None:
        pass

def run_demo():
    print("\n" + "="*80)
    print("🔥 RUNAWAY AUTONOMOUS PROCUREMENT DEMO 🔥")
    print("="*80 + "\n")
    
    with tempfile.TemporaryDirectory(prefix="prod_procurement_audit_") as tmpdir:
        audit_store = DeltaAuthorizationAuditStore(Path(tmpdir) / "audit_log")
        store = InMemoryStateStore()
        store.save(State("LPT-99", "hardware", {"stock": 4, "threshold": 10}))
        
        agent = Agent(
            state_store=store,
            policy_engine=ProcurementPolicyEngine(audit_store=audit_store),
            reasoner=RunawayReasoner(),
            action_executor=NullExecutor(),
            action_factory=lambda d: decision_to_action(d, d.decision_type)
        )
        
        # Trigger the autonomous loop
        event = Event("inventory.level.changed", "LPT-99", "hardware", {"stock": 4}, source="warehouse")
        
        time.sleep(1)
        print("📦 EVENT RECEIVED: Stock level for LPT-99 dropped to 4.")
        time.sleep(1.5)
        
        result = agent.process(event)
        
        print("\n🧠 [COLUMN 1: THE AI REASONER]")
        rec = result.recommendations[0]
        print(f"   Reasoning: {rec.rationale}")
        print(f"   Proposal: {rec.recommendation_type} | QTY: {rec.parameters['quantity']}")
        print("   Status: ⚠️ PENDING AUTHORIZATION")
        
        time.sleep(2)
        
        print("\n🛡️  [COLUMN 2: THE POLICY ENGINE]")
        denial = next(d for d in result.decisions if d.decision_type == "DENY")
        print("   Evaluating proposed action against deterministic limits...")
        print(f"   Rule Check: FAILED. {denial.reason}")
        print("   Action status: 🛑 BLOCKED BEFORE EXECUTION")
        
        time.sleep(2)
        
        print("\n🗄️  [COLUMN 3: THE DELTA AUDIT LEDGER]")
        # Force a record to ensure Delta is fully flushed for reading
        audit_store.record("SYSTEM_CHECK", detail="Verifying Delta Table logs...") 
        
        from deltalake import DeltaTable
        logs = DeltaTable(audit_store.path).to_pandas()
        denial_log = logs[logs['outcome'] == 'POLICY_DENIED'].iloc[0]
        print(f"   [TIMESTAMP]  {denial_log['recorded_at']}")
        print(f"   [OUTCOME]    {denial_log['outcome']}")
        print(f"   [PRINCIPAL]  {denial_log['principal_id']}")
        print(f"   [DETAIL]     {denial_log['detail']}")
        
        print("\n" + "="*80)
        print("✅ DEMO COMPLETE: Balance sheet protected. Zero unauthorized API calls made.")
        print("="*80 + "\n")

# This is the hook that was likely missing!
if __name__ == "__main__":
    run_demo()