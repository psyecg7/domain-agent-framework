package dev.domainagent.sdk;

import dev.domainagent.sdk.grpc.CommandAcknowledgement;
import dev.domainagent.sdk.grpc.InventoryCommand;
import dev.domainagent.sdk.grpc.InventoryCommandGatewayGrpc;
import io.grpc.stub.StreamObserver;

/** Server-side gRPC adapter: acknowledge only durable Inventory inbox acceptance. */
public final class GrpcInventoryCommandGateway extends InventoryCommandGatewayGrpc.InventoryCommandGatewayImplBase {
  private static final int PROTOCOL_VERSION = 1;
  private final InventoryCommandInbox inbox;

  public GrpcInventoryCommandGateway(InventoryCommandInbox inbox) {
    if (inbox == null) throw new IllegalArgumentException("inbox is required");
    this.inbox = inbox;
  }

  @Override
  public StreamObserver<InventoryCommand> submit(StreamObserver<CommandAcknowledgement> responses) {
    return new StreamObserver<>() {
      @Override
      public void onNext(InventoryCommand command) {
        try {
          if (command.getProtocolVersion() != PROTOCOL_VERSION) {
            responses.onNext(receipt(command, CommandAcknowledgement.Status.REJECTED,
                "unsupported Inventory command protocol version: " + command.getProtocolVersion()));
            return;
          }
          CommandAcceptance acceptance = inbox.accept(command);
          CommandAcknowledgement.Status status = acceptance == CommandAcceptance.ACCEPTED
              ? CommandAcknowledgement.Status.ACCEPTED : CommandAcknowledgement.Status.DUPLICATE;
          responses.onNext(receipt(command, status, ""));
        } catch (IllegalArgumentException error) {
          responses.onNext(receipt(command, CommandAcknowledgement.Status.REJECTED, error.getMessage()));
        } catch (RuntimeException error) {
          responses.onError(error);
        }
      }

      @Override public void onError(Throwable error) { /* Client reconnects with stable command identity. */ }
      @Override public void onCompleted() { responses.onCompleted(); }
    };
  }

  private static CommandAcknowledgement receipt(
      InventoryCommand command, CommandAcknowledgement.Status status, String detail) {
    return CommandAcknowledgement.newBuilder()
        .setProtocolVersion(PROTOCOL_VERSION)
        .setEventId(command.getEventId())
        .setOperationId(command.getOperationId())
        .setAttemptId(command.getAttemptId())
        .setStatus(status)
        .setDetail(detail == null ? "" : detail)
        .build();
  }
}
