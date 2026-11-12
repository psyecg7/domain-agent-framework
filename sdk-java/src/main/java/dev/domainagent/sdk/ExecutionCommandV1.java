package dev.domainagent.sdk;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/** Exact executor request defined by execution-command-v1.schema.json. */
public record ExecutionCommandV1(
    String actionId,
    String decisionId,
    String actionType,
    String entityId,
    String entityType,
    Map<String, Object> parameters,
    Map<String, Object> preconditions,
    String idempotencyKey
) {
  public ExecutionCommandV1 {
    required(actionId, "actionId"); required(decisionId, "decisionId");
    required(actionType, "actionType"); required(entityId, "entityId"); required(entityType, "entityType");
    parameters = Collections.unmodifiableMap(new LinkedHashMap<>(parameters == null ? Map.of() : parameters));
    preconditions = Collections.unmodifiableMap(new LinkedHashMap<>(preconditions == null ? Map.of() : preconditions));
  }

  public Map<String, Object> binding() {
    Map<String, Object> binding = new LinkedHashMap<>();
    binding.put("action_id", actionId); binding.put("decision_id", decisionId);
    binding.put("action_type", actionType); binding.put("entity_id", entityId); binding.put("entity_type", entityType);
    binding.put("parameters_sha256", CanonicalJson.sha256(parameters));
    binding.put("preconditions_sha256", CanonicalJson.sha256(preconditions));
    binding.put("idempotency_key", idempotencyKey);
    return binding;
  }

  public Map<String, Object> wire() {
    Map<String, Object> wire = new LinkedHashMap<>(binding());
    wire.put("parameters", parameters); wire.put("preconditions", preconditions);
    return wire;
  }

  @SuppressWarnings("unchecked")
  public static ExecutionCommandV1 fromWire(Map<String, Object> wire) {
    Object parameters = wire.get("parameters"), preconditions = wire.getOrDefault("preconditions", Map.of());
    if (!(parameters instanceof Map<?, ?> rawParameters) || !(preconditions instanceof Map<?, ?> rawPreconditions)) {
      throw new ProtocolException("Execution command parameters and preconditions must be JSON objects");
    }
    return new ExecutionCommandV1(
        string(wire, "action_id"), string(wire, "decision_id"), string(wire, "action_type"),
        string(wire, "entity_id"), string(wire, "entity_type"), map(rawParameters), map(rawPreconditions), optionalString(wire, "idempotency_key"));
  }

  private static void required(String value, String name) {
    if (value == null || value.isBlank()) throw new IllegalArgumentException(name + " is required");
  }
  static String string(Map<String, Object> values, String name) {
    String value = optionalString(values, name);
    if (value == null) throw new ProtocolException(name + " is required");
    return value;
  }
  static String optionalString(Map<String, Object> values, String name) {
    Object value = values.get(name);
    if (value == null) return null;
    if (!(value instanceof String text) || text.isBlank()) throw new ProtocolException(name + " must be a non-empty string");
    return text;
  }
  static Map<String, Object> map(Map<?, ?> raw) {
    Map<String, Object> result = new LinkedHashMap<>();
    raw.forEach((key, value) -> {
      if (!(key instanceof String text)) throw new ProtocolException("JSON object keys must be strings");
      result.put(text, value);
    });
    return result;
  }
}
