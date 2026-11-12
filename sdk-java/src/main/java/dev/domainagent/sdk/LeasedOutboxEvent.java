package dev.domainagent.sdk;

/** One PostgreSQL outbox row exclusively leased to a publisher worker. */
public record LeasedOutboxEvent(String eventId, String operationId, EventEnvelopeV1 event, String workerId) {}
