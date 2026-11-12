package dev.domainagent.sdk;

import static org.junit.jupiter.api.Assertions.assertEquals;

import dev.domainagent.sdk.grpc.CommandAcknowledgement;
import dev.domainagent.sdk.grpc.InventoryCommand;
import dev.domainagent.sdk.grpc.InventoryCommandGatewayGrpc;
import io.grpc.ManagedChannel;
import io.grpc.inprocess.InProcessChannelBuilder;
import io.grpc.inprocess.InProcessServerBuilder;
import io.grpc.Server;
import java.time.Duration;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/** Verifies the same Java stream accepts a burst without waiting for outcomes. */
class GrpcInventoryCommandGatewayTest {
  @Test
  void oneStreamAcceptsManyCommandsAndMakesAReconnectDuplicateSafe() throws Exception {
    Map<String, InventoryCommand> accepted = new HashMap<>();
    InventoryCommandInbox inbox = command -> {
      String key = command.getOperationId() + ":" + command.getAttemptId();
      InventoryCommand existing = accepted.putIfAbsent(key, command);
      if (existing == null) return CommandAcceptance.ACCEPTED;
      if (existing.equals(command)) return CommandAcceptance.DUPLICATE;
      throw new IllegalArgumentException("command identity conflicts");
    };
    String name = InProcessServerBuilder.generateName();
    Server server = InProcessServerBuilder.forName(name)
        .directExecutor()
        .addService(new GrpcInventoryCommandGateway(inbox))
        .build()
        .start();
    ManagedChannel channel = InProcessChannelBuilder.forName(name).directExecutor().build();
    try {
      GrpcInventoryCommandClient client = new GrpcInventoryCommandClient(
          InventoryCommandGatewayGrpc.newStub(channel));
      List<InventoryCommand> commands = java.util.stream.IntStream.range(0, 200)
          .mapToObj(GrpcInventoryCommandGatewayTest::command)
          .collect(java.util.stream.Collectors.toCollection(java.util.ArrayList::new));
      commands.add(command(0));

      List<CommandAcknowledgement> receipts = client.submitBatch(commands, Duration.ofSeconds(2));

      assertEquals(201, receipts.size());
      assertEquals(CommandAcknowledgement.Status.ACCEPTED, receipts.get(0).getStatus());
      assertEquals(CommandAcknowledgement.Status.DUPLICATE, receipts.get(200).getStatus());
      assertEquals(200, accepted.size());
    } finally {
      channel.shutdownNow();
      server.shutdownNow();
    }
  }

  private static InventoryCommand command(int number) {
    String orderId = "ORD-" + number;
    String operationId = orderId + ":inventory-reallocation";
    return InventoryCommand.newBuilder()
        .setProtocolVersion(1)
        .setEventId("event-" + number)
        .setCorrelationId("order:" + orderId)
        .setCausationId("order-submitted:" + orderId)
        .setOperationId(operationId)
        .setAttemptId(operationId + ":attempt-1")
        .setOrderId(orderId)
        .setSku("SKU-" + number)
        .setTargetWarehouse("WH-A")
        .setQuantity(1)
        .setExpectedStateVersion(1)
        .build();
  }
}
