package dev.domainagent.sdk;

/** Optional durable decision revocation lookup performed immediately before an effect. */
@FunctionalInterface
public interface RevocationStore {
  boolean isRevoked(String decisionId, long nowEpochSeconds);
}
