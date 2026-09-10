# agent-scheduler

`InMemoryEventScheduler` is a deliberately small adapter for publishing delayed
events. It is suitable for local development and tests; production deployments
must provide a durable scheduler implementation with their own delivery and
deduplication contract.
