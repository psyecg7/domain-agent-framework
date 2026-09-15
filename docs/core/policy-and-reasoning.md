# Policy and reasoning

The framework separates advice from authority.

```text
Reasoner → Recommendation → deterministic PolicyEngine → Decision
```

A `Reasoner` can be an LLM, a rules-based helper, or any other component that
returns `Recommendation` values. Recommendations have no authority: they
cannot create an `Action` directly.

`PolicyEngine` is deterministic domain code. It evaluates current `State` and,
when it implements `evaluate_recommendations`, decides which recommendations
are acceptable. It returns zero or more `Decision` values.

For example, an LLM can recommend `INVESTIGATE_SUPPLIER_DELAY`. Policy still
checks the current order, stock, approval, and risk facts before deciding
whether that recommendation is allowed.

## Two reasoner shapes

The current core accepts both forms for compatibility:

```python
reasoner.reason(context)                         # produces recommendations
reasoner.reason(state, existing_decisions)        # legacy composition form
```

The first form receives `ReasoningContext`: current state, the observations
from this event, optional memory matches, and event metadata. It is the form to
use for new AI integrations.

Prompt injection and poor model output remain model-behavior risks. The core's
claim is narrower: even adversarial advice must pass deterministic policy before
it can become a Decision or Action.
