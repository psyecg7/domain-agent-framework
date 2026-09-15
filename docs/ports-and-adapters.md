# Ports and adapters

The word “port” is only a programming term for a small interface. Think of it
as the shape of a plug socket.

An adapter is the plug that fits that socket and uses a real technology.

```text
agent-core says: "I need something that can load and save State."
                         │
                         │ StateStore port: get(...) and save(...)
                         ▼
storage-postgres supplies: "I use PostgreSQL to do that."
```

The core knows the `StateStore` method names. It does not know SQL, connection
strings, tables, or PostgreSQL. `storage-postgres` knows those details and fits
into the core through the `StateStore` port.

## Package names describe the job, not an agent

Only an application you build, such as an Order service or an Inventory service,
is an agent. `agent-core` and `agent-app` are framework libraries. Optional
packages start with their role, so their names tell you what they connect or
provide:

| Package | Plain meaning | Is it an agent? |
| --- | --- | --- |
| `agent-core` | The small event-to-action runtime. | No. It is a library. |
| `agent-app` | The easy local API used to build an agent. | No. It is a library. |
| `storage-postgres` / `storage-delta` | Durable business state or durable evidence. | No. They are storage integrations. |
| `transport-redpanda` | Redpanda/Kafka event delivery between services. | No. It is a transport integration. |
| `memory-lancedb` | Optional document and note search. | No. It is a memory integration. |
| `model-openai` / `model-ollama` | Asking an AI model for non-authoritative advice. | No. They are model integrations. |
| `scheduler` | Local delayed event emission. | No. It is a scheduling utility. |
| `enterprise` | A reference for separately deployed Policy and Executor services. | No. It is a security reference. |
| `conformance` | Reusable domain-safety assertions for CI. | No. It is test-only. |

They are all adapters in the broad architectural sense: each connects the
runtime or an application to a concrete technology or deployment concern. The
role prefix is more useful than that broad word when choosing a package. In
particular, a model produces advice; it does not store facts or deliver events.

## The three common ports

| The core needs | Port name | An adapter can provide it |
| --- | --- | --- |
| Current facts about an order, product, or sensor | `StateStore` | PostgreSQL or Delta |
| Optional related text for an AI | `MemoryStore` | LanceDB |
| A place to send an already-approved action | `ActionExecutor` | Your Python function, a publisher, or a domain integration |

Redpanda is slightly different. It moves events between separate applications;
it is not the owner of State and it is not an AI memory store. The framework
uses it when an Order agent and an Inventory agent run in different processes or
on different machines.

## One example

```text
Order agent
  uses PostgreSQL to save Order facts
  uses Redpanda to send a reservation request to Inventory

Inventory agent
  uses PostgreSQL to save stock facts
  uses Redpanda to send a result back to Order

Optional: either agent may use LanceDB to search helpful notes before an AI
reasoner makes a recommendation. LanceDB does not decide stock or orders.
```

Start with `AgentApp` and its in-memory state store. Add PostgreSQL when state
must survive restart. Add Redpanda when two applications must exchange events.
Add LanceDB only when an AI needs to search supporting text.
