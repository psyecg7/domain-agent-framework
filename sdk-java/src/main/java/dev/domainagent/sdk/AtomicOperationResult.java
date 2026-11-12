package dev.domainagent.sdk;

/** Durable outcome of one domain-owned operation. */
public record AtomicOperationResult(String operationId, String outcome, boolean duplicate, String outboxEventId) {}
