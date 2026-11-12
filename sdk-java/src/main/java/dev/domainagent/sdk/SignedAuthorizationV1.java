package dev.domainagent.sdk;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/** Portable Ed25519 authorization envelope. */
public record SignedAuthorizationV1(Map<String, Object> claims, String signature) {
  public SignedAuthorizationV1 {
    claims = Collections.unmodifiableMap(new LinkedHashMap<>(claims == null ? Map.of() : claims));
    if (signature == null || signature.isBlank()) throw new IllegalArgumentException("signature is required");
  }

  public static SignedAuthorizationV1 fromWire(Map<String, Object> wire) {
    Object claims = wire.get("claims");
    if (!(claims instanceof Map<?, ?> raw)) throw new ProtocolException("Authorization claims must be a JSON object");
    return new SignedAuthorizationV1(ExecutionCommandV1.map(raw), ExecutionCommandV1.string(wire, "signature"));
  }
}
