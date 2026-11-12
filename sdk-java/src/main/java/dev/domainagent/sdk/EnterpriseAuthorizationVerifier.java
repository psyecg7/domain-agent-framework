package dev.domainagent.sdk;

import java.nio.charset.StandardCharsets;
import java.security.KeyFactory;
import java.security.PublicKey;
import java.security.Signature;
import java.security.spec.X509EncodedKeySpec;
import java.util.Base64;
import java.util.Map;
import java.util.Set;

/** Fail-closed Enterprise authorization verifier for a Java Executor. */
public final class EnterpriseAuthorizationVerifier {
  private final Map<String, PublicKey> publicKeys;
  private final String audience;
  private final ReplayStore replayStore;
  private final RevocationStore revocationStore;
  private final long clockSkewSeconds;

  public EnterpriseAuthorizationVerifier(
      Map<String, PublicKey> publicKeys, String audience, ReplayStore replayStore,
      RevocationStore revocationStore, long clockSkewSeconds) {
    if (audience == null || audience.isBlank()) throw new IllegalArgumentException("audience is required");
    if (replayStore == null) throw new IllegalArgumentException("replayStore is required");
    if (clockSkewSeconds < 0 || clockSkewSeconds > 60) throw new IllegalArgumentException("clock skew must be from 0 to 60 seconds");
    this.publicKeys = Map.copyOf(publicKeys);
    this.audience = audience;
    this.replayStore = replayStore;
    this.revocationStore = revocationStore;
    this.clockSkewSeconds = clockSkewSeconds;
  }

  public static PublicKey ed25519PublicKeyFromPem(String pem) {
    try {
      String body = pem.replace("-----BEGIN PUBLIC KEY-----", "")
          .replace("-----END PUBLIC KEY-----", "").replaceAll("\\s", "");
      return KeyFactory.getInstance("Ed25519").generatePublic(new X509EncodedKeySpec(Base64.getDecoder().decode(body)));
    } catch (Exception error) {
      throw new IllegalArgumentException("Public key is not Ed25519 PEM", error);
    }
  }

  public void verify(ExecutionCommandV1 command, SignedAuthorizationV1 authorization, long nowEpochSeconds, Set<String> ownedActionTypes) {
    Map<String, Object> claims = authorization.claims();
    String keyId = ExecutionCommandV1.string(claims, "key_id");
    PublicKey key = publicKeys.get(keyId);
    if (key == null) throw new AuthorizationException("Authorization key is unknown");
    verifySignature(key, claims, authorization.signature());

    if (integer(claims, "version") != 1 || !audience.equals(ExecutionCommandV1.optionalString(claims, "audience"))) {
      throw new AuthorizationException("Authorization is not intended for this Executor");
    }
    long issuedAt = integer(claims, "issued_at"), expiresAt = integer(claims, "expires_at");
    if (issuedAt > nowEpochSeconds + clockSkewSeconds || expiresAt <= nowEpochSeconds - clockSkewSeconds) {
      throw new AuthorizationException("Authorization is expired or has an invalid time window");
    }
    for (Map.Entry<String, Object> entry : command.binding().entrySet()) {
      if (!java.util.Objects.equals(entry.getValue(), claims.get(entry.getKey()))) {
        throw new AuthorizationException("Authorization does not bind this execution command");
      }
    }
    String decisionId = ExecutionCommandV1.string(claims, "decision_id");
    if (revocationStore != null && revocationStore.isRevoked(decisionId, nowEpochSeconds)) {
      throw new AuthorizationException("Authorization decision has been revoked");
    }
    if (ownedActionTypes != null && !ownedActionTypes.contains(command.actionType())) {
      throw new AuthorizationException("Executor does not own action type " + command.actionType());
    }
    String authorizationId = ExecutionCommandV1.string(claims, "authorization_id");
    if (!replayStore.claim(authorizationId, expiresAt)) throw new AuthorizationException("Authorization has already been used");
  }

  private static void verifySignature(PublicKey key, Map<String, Object> claims, String encodedSignature) {
    try {
      Signature verifier = Signature.getInstance("Ed25519");
      verifier.initVerify(key);
      verifier.update(CanonicalJson.utf8(claims));
      if (!verifier.verify(Base64.getUrlDecoder().decode(encodedSignature.getBytes(StandardCharsets.US_ASCII)))) {
        throw new AuthorizationException("Authorization signature is invalid");
      }
    } catch (AuthorizationException error) {
      throw error;
    } catch (Exception error) {
      throw new AuthorizationException("Authorization signature is invalid", error);
    }
  }

  private static long integer(Map<String, Object> values, String name) {
    Object value = values.get(name);
    if (!(value instanceof Number number) || Math.floor(number.doubleValue()) != number.doubleValue()) {
      throw new AuthorizationException(name + " must be an integer");
    }
    return number.longValue();
  }
}
