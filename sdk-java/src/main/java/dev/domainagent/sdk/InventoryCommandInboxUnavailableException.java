package dev.domainagent.sdk;

/** The shared Inventory command inbox could not complete a storage operation. */
public final class InventoryCommandInboxUnavailableException extends RuntimeException {
  public InventoryCommandInboxUnavailableException(String message, Throwable cause) {
    super(message, cause);
  }
}
