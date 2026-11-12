package dev.domainagent.sdk;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.json.JsonMapper;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

/** JSON codec for protocol/events/event-envelope-v1.schema.json. */
public final class EventEnvelopeCodec {
  private static final Set<String> RESERVED = Set.of(
      "protocol_version", "event_id", "event_type", "entity_id", "entity_type",
      "occurred_at", "source", "idempotency_key", "metadata", "_transport");
  private final ObjectMapper json = JsonMapper.builder().build();

  public String encode(EventEnvelopeV1 event) {
    try {
      return json.writeValueAsString(toRecord(event));
    } catch (JsonProcessingException error) {
      throw new ProtocolException("Event is not JSON serializable", error);
    }
  }

  @SuppressWarnings("unchecked")
  public EventEnvelopeV1 decode(String body) {
    try {
      Object value = json.readValue(body, Object.class);
      if (!(value instanceof Map<?, ?> raw)) throw new ProtocolException("Event record must be a JSON object");
      Map<String, Object> record = new LinkedHashMap<>();
      for (Map.Entry<?, ?> entry : raw.entrySet()) {
        if (!(entry.getKey() instanceof String key)) throw new ProtocolException("Event record key must be a string");
        record.put(key, entry.getValue());
      }
      return fromRecord(record);
    } catch (JsonProcessingException error) {
      throw new ProtocolException("Event record is not valid JSON", error);
    }
  }

  @SuppressWarnings("unchecked")
  public EventEnvelopeV1 fromRecord(Map<String, Object> record) {
    Object version = record.get("protocol_version");
    if (version != null && (!(version instanceof Number) || ((Number) version).intValue() != EventEnvelopeV1.PROTOCOL_VERSION)) {
      throw new ProtocolException("Unsupported event protocol version: " + version);
    }
    Map<String, Object> metadata = record.get("metadata") instanceof Map<?, ?> map
        ? castMap(map) : Map.of();
    Map<String, Object> data = new LinkedHashMap<>(record);
    RESERVED.forEach(data::remove);
    try {
      return new EventEnvelopeV1(
          required(record, "event_id"), required(record, "event_type"),
          required(record, "entity_id"), required(record, "entity_type"),
          Instant.parse(required(record, "occurred_at")), optional(record, "source"),
          optional(record, "idempotency_key"), metadata, data);
    } catch (IllegalArgumentException error) {
      throw new ProtocolException("Event record is malformed", error);
    }
  }

  public Map<String, Object> toRecord(EventEnvelopeV1 event) {
    Map<String, Object> record = new LinkedHashMap<>(event.data());
    for (String reserved : RESERVED) {
      if (record.containsKey(reserved)) throw new ProtocolException("Domain data may not use reserved field " + reserved);
    }
    record.put("protocol_version", EventEnvelopeV1.PROTOCOL_VERSION);
    record.put("event_id", event.eventId());
    record.put("event_type", event.eventType());
    record.put("entity_id", event.entityId());
    record.put("entity_type", event.entityType());
    record.put("occurred_at", event.occurredAt().toString());
    if (event.source() != null) record.put("source", event.source());
    if (event.idempotencyKey() != null) record.put("idempotency_key", event.idempotencyKey());
    if (!event.metadata().isEmpty()) record.put("metadata", event.metadata());
    return record;
  }

  private static String required(Map<String, Object> value, String name) {
    String result = optional(value, name);
    if (result == null) throw new IllegalArgumentException(name + " is required");
    return result;
  }

  private static String optional(Map<String, Object> value, String name) {
    Object result = value.get(name);
    if (result == null) return null;
    if (!(result instanceof String text) || text.isBlank()) throw new IllegalArgumentException(name + " must be a non-empty string");
    return text;
  }

  private static Map<String, Object> castMap(Map<?, ?> raw) {
    Map<String, Object> result = new LinkedHashMap<>();
    raw.forEach((key, value) -> {
      if (!(key instanceof String text)) throw new ProtocolException("metadata keys must be strings");
      result.put(text, value);
    });
    return result;
  }
}
