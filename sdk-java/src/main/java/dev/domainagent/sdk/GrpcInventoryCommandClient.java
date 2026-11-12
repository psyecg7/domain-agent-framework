package dev.domainagent.sdk;

import dev.domainagent.sdk.grpc.CommandAcknowledgement;
import dev.domainagent.sdk.grpc.InventoryCommand;
import dev.domainagent.sdk.grpc.InventoryCommandGatewayGrpc;
import io.grpc.StatusRuntimeException;
import io.grpc.stub.StreamObserver;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Client adapter that sends one durable Order outbox batch on a gRPC stream. */
public final class GrpcInventoryCommandClient {
  private final InventoryCommandGatewayGrpc.InventoryCommandGatewayStub stub;

  public GrpcInventoryCommandClient(InventoryCommandGatewayGrpc.InventoryCommandGatewayStub stub) {
    if (stub == null) throw new IllegalArgumentException("stub is required");
    this.stub = stub;
  }

  /**
   * Send many commands over one connection and await acceptance receipts.
   *
   * <p>Call this only after the Order domain persisted its own outbox. A receipt
   * is an acceptance result, not the final Inventory business outcome.</p>
   */
  public List<CommandAcknowledgement> submitBatch(List<InventoryCommand> commands, Duration timeout) {
    if (commands == null) throw new IllegalArgumentException("commands are required");
    if (timeout == null || timeout.isNegative() || timeout.isZero()) {
      throw new IllegalArgumentException("timeout must be positive");
    }
    List<CommandAcknowledgement> receipts = new ArrayList<>();
    AtomicReference<Throwable> failure = new AtomicReference<>();
    CountDownLatch completed = new CountDownLatch(1);
    StreamObserver<CommandAcknowledgement> responses = new StreamObserver<>() {
      @Override public void onNext(CommandAcknowledgement receipt) { synchronized (receipts) { receipts.add(receipt); } }
      @Override public void onError(Throwable error) { failure.set(error); completed.countDown(); }
      @Override public void onCompleted() { completed.countDown(); }
    };
    StreamObserver<InventoryCommand> requests = stub.submit(responses);
    try {
      for (InventoryCommand command : commands) requests.onNext(command);
      requests.onCompleted();
      if (!completed.await(timeout.toMillis(), TimeUnit.MILLISECONDS)) {
        throw new IllegalStateException("Timed out waiting for Inventory command acknowledgements");
      }
    } catch (InterruptedException error) {
      Thread.currentThread().interrupt();
      throw new IllegalStateException("Interrupted waiting for Inventory command acknowledgements", error);
    } catch (StatusRuntimeException error) {
      throw new IllegalStateException("Inventory command stream failed", error);
    }
    if (failure.get() != null) throw new IllegalStateException("Inventory command stream failed", failure.get());
    synchronized (receipts) {
      if (receipts.size() != commands.size()) {
        throw new IllegalStateException("Inventory command stream ended without acknowledging every command");
      }
      return List.copyOf(receipts);
    }
  }
}
