from agent_enterprise import AuthorizationError, ExecutionCommand, ExecutorAuthorizationVerifier, InMemoryReplayStore, PolicyAuthorizationIssuer
from agent_core import Decision, decision_to_action


class Inventory:
    def __init__(self): self.available, self.version, self.effects = 1, 5, []
    def reserve(self, command):
        expected = command.preconditions
        quantity = command.parameters["quantity"]
        if (
            expected.get("version") != self.version
            or expected.get("available") != self.available
            or not isinstance(quantity, int)
            or quantity < 1
            or quantity > self.available
        ):
            return "CONFLICT"
        self.available -= quantity; self.version += 1; self.effects.append(command.action_id)
        return "SUCCEEDED"


def command(decision_id, *, quantity=1):
    d = Decision("SKU-1", "inventory", "RESERVE", "HIGH", "stock checked", decision_id=decision_id)
    a = decision_to_action(d, "RESERVE", parameters={"quantity": quantity})
    return ExecutionCommand.from_action(a, d, preconditions={"version": 5, "available": 1})


def test_stale_signed_precondition_becomes_domain_conflict_without_second_effect():
    issuer = PolicyAuthorizationIssuer.generate(key_id="policy", issuer="policy")
    verifier = ExecutorAuthorizationVerifier.from_pem({"policy": issuer.public_key_pem()}, audience="inventory", replay_store=InMemoryReplayStore())
    inventory = Inventory()
    first, second = command("D1"), command("D2")
    verifier.verify(first, issuer.authorize(first, audience="inventory"))
    assert inventory.reserve(first) == "SUCCEEDED"
    verifier.verify(second, issuer.authorize(second, audience="inventory"))
    assert inventory.reserve(second) == "CONFLICT"
    assert inventory.available == 0 and len(inventory.effects) == 1


def test_altered_precondition_invalidates_authorization():
    issuer = PolicyAuthorizationIssuer.generate(key_id="policy", issuer="policy")
    verifier = ExecutorAuthorizationVerifier.from_pem({"policy": issuer.public_key_pem()}, audience="inventory", replay_store=InMemoryReplayStore())
    original = command("D1")
    signed = issuer.authorize(original, audience="inventory")
    altered = ExecutionCommand(**{**original.__dict__, "preconditions": {"version": 4, "available": 1}})
    try: verifier.verify(altered, signed)
    except AuthorizationError: pass
    else: raise AssertionError("altered precondition was accepted")


def test_capacity_is_an_atomic_domain_precondition_not_just_a_signed_fact():
    issuer = PolicyAuthorizationIssuer.generate(key_id="policy", issuer="policy")
    verifier = ExecutorAuthorizationVerifier.from_pem({"policy": issuer.public_key_pem()}, audience="inventory", replay_store=InMemoryReplayStore())
    inventory = Inventory()
    oversized = command("D1", quantity=2)

    verifier.verify(oversized, issuer.authorize(oversized, audience="inventory"))

    assert inventory.reserve(oversized) == "CONFLICT"
    assert inventory.available == 1 and inventory.effects == []
