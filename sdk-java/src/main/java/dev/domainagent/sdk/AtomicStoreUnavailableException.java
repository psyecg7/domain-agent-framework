package dev.domainagent.sdk;

/** PostgreSQL could not be read or committed; it is not a business conflict. */
public final class AtomicStoreUnavailableException extends RuntimeException {
  public AtomicStoreUnavailableException(String message, Throwable cause) { super(message, cause); }
}
