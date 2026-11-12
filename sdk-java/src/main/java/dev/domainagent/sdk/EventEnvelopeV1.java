package dev.domainagent.sdk;

import java.time.Instant;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/** Versioned, language-neutral broker event. Domain data is owned by the event type. */
public record EventEnvelopeV1(
    String eventId,
    String eventType,
    String entityId,
    String entityType,
    Instant occurredAt,
    String source,
    String idempotencyKey,
    Map<String, Object> metadata,
    Map<String, Object> data
) {
  public static final int PROTOCOL_VERSION = 1;

  public EventEnvelopeV1 {
    require(eventId, "eventId");
    require(eventType, "eventType");
    require(entityId, "entityId");
    require(entityType, "entityType");
    if (occurredAt == null) throw new IllegalArgumentException("occurredAt is required");
    metadata = Collections.unmodifiableMap(new LinkedHashMap<>(metadata == null ? Map.of() : metadata));
    data = Collections.unmodifiableMap(new LinkedHashMap<>(data == null ? Map.of() : data));
  }

  private static void require(String value, String name) {
    if (value == null || value.isBlank()) throw new IllegalArgumentException(name + " is required");
  }
}
