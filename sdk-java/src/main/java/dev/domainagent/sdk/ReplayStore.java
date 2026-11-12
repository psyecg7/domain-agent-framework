package dev.domainagent.sdk;

/** Durable implementations atomically claim an authorization across Executor replicas. */
@FunctionalInterface
public interface ReplayStore {
  boolean claim(String authorizationId, long expiresAtEpochSeconds);
}
