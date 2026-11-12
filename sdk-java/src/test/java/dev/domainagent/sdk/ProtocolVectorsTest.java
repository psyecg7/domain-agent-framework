package dev.domainagent.sdk;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.Map;
import org.junit.jupiter.api.Test;

/** The Java SDK must consume the exact protocol fixtures shared with Python. */
class ProtocolVectorsTest {
  private static final ObjectMapper JSON = new ObjectMapper();

  @Test
  void eventEnvelopeV1DecodesTheSharedVector() throws Exception {
    Map<String, Object> vector = read("events/event-envelope-v1.json");
    @SuppressWarnings("unchecked") Map<String, Object> record = (Map<String, Object>) vector.get("record");

    EventEnvelopeV1 event = new EventEnvelopeCodec().fromRecord(record);

    assertEquals("evt-0001", event.eventId());
    assertEquals("inventory.stock_changed", event.eventType());
    assertEquals("SKU-1", event.entityId());
    assertEquals(7, event.data().get("available_quantity"));
  }

  @Test
  void enterpriseAuthorizationV1AcceptsAndRejectsTheSharedVector() throws Exception {
    Map<String, Object> vector = read("enterprise/authorization-v1.json");
    @SuppressWarnings("unchecked") Map<String, Object> commandWire = (Map<String, Object>) vector.get("command");
    @SuppressWarnings("unchecked") Map<String, Object> authorizationWire = (Map<String, Object>) vector.get("authorization");
    ExecutionCommandV1 command = ExecutionCommandV1.fromWire(commandWire);
    SignedAuthorizationV1 authorization = SignedAuthorizationV1.fromWire(authorizationWire);
    long now = ((Number) vector.get("verification_now")).longValue();

    assertEquals(vector.get("canonical_claims_utf8"), new String(CanonicalJson.utf8(authorization.claims()), StandardCharsets.UTF_8));
    verifier(vector).verify(command, authorization, now, java.util.Set.of("RESERVE_INVENTORY"));

    @SuppressWarnings("unchecked") Map<String, Object> tampered = (Map<String, Object>) vector.get("tampered_command");
    assertThrows(AuthorizationException.class, () -> verifier(vector).verify(
        ExecutionCommandV1.fromWire(tampered), authorization, now, java.util.Set.of("RESERVE_INVENTORY")));

    @SuppressWarnings("unchecked") Map<String, Object> expected = (Map<String, Object>) vector.get("expected");
    assertThrows(AuthorizationException.class, () -> verifier(vector).verify(
        command, authorization, ((Number) expected.get("expired_at")).longValue(), java.util.Set.of("RESERVE_INVENTORY")));
  }

  private static EnterpriseAuthorizationVerifier verifier(Map<String, Object> vector) {
    return new EnterpriseAuthorizationVerifier(
        Map.of("vector-key-1", EnterpriseAuthorizationVerifier.ed25519PublicKeyFromPem((String) vector.get("public_key_pem"))),
        "example-executor", (authorizationId, expiresAt) -> true, null, 5);
  }

  private static Map<String, Object> read(String name) throws Exception {
    Path path = Path.of("..", "protocol", "test-vectors").resolve(name);
    return JSON.readValue(path.toFile(), new TypeReference<>() {});
  }
}
