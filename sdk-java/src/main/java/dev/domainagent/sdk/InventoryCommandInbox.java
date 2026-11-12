package dev.domainagent.sdk;

import dev.domainagent.sdk.grpc.InventoryCommand;

/** Inventory-owned boundary that records a command before a gRPC ack is sent. */
@FunctionalInterface
public interface InventoryCommandInbox {
  CommandAcceptance accept(InventoryCommand command);
}
