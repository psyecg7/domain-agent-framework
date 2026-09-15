# Core reference

`agent-core` supplies the vocabulary and lifecycle that every application and
adapter shares. It does not provide a database, broker, AI model, business
rule, retry policy, or workflow engine.

Read the pages in this order when learning the framework:

1. [Lifecycle](lifecycle.md) — what `Agent.process(event)` does.
2. [State and storage](state-and-storage.md) — current facts and the
   `StateStore` boundary.
3. [Policy and reasoning](policy-and-reasoning.md) — why AI advice cannot
   authorize an action.
4. [Decisions and actions](decisions-and-actions.md) — the execution boundary.
5. [Events and capabilities](events-and-capabilities.md) — communicating with
   another domain without calling it directly.
6. [Memory](memory.md) — optional semantic context and where LanceDB fits.
7. [Ports](ports.md) — the interfaces adapters implement.

For the local developer API, start with [`agent-app`](../../agent-app/README.md).
For the package and adapter selection guide, read the
[component map](../components.md).
