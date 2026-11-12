package dev.domainagent.sdk;

/** Missing, altered, expired, misdirected, revoked, or replayed authorization. */
public final class AuthorizationException extends RuntimeException {
  public AuthorizationException(String message) { super(message); }
  public AuthorizationException(String message, Throwable cause) { super(message, cause); }
}
