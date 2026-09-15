# model-openai

`model-openai` lets an application ask an OpenAI model for two kinds of
advice:

- `OpenAIReasoner` returns `Recommendation` values from a `ReasoningContext`.
- `OpenAIIntentInterpreter` turns a human request into a declarative `Intent`.

Neither class decides, executes, selects a capability, or writes state. The
application's deterministic policy evaluates recommendations, and a capability
registry plus the receiving domain handle an intent.

```text
OpenAI model → Recommendation or Intent → application/domain policy → Decision → Action
```

Use this adapter only when an OpenAI model is a useful source of advice. It is
optional. The local `agent-app` example works without an AI model.

## Usage

```python
from model_openai import OpenAIChatClient, OpenAIReasoner

client = OpenAIChatClient(model="your-model-name")
reasoner = OpenAIReasoner(client)
```

Pass `reasoner` to the advanced raw `Agent` API with your deterministic
`PolicyEngine`. The policy remains responsible for accepting or rejecting every
recommendation.

OpenAI responses must be JSON with the expected shape. Invalid or empty output
raises `OpenAIResponseError` and must be handled by the application boundary.
