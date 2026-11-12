package dev.domainagent.sdk;

import java.util.List;
import java.util.function.Function;

/** Publish leased outbox rows after their domain transaction has committed. */
public final class PostgresOutboxPublisher {
  private final PostgresAtomicOperationStore store;
  private final KafkaEventPublisher publisher;
  private final Function<EventEnvelopeV1, String> topicForEvent;

  public PostgresOutboxPublisher(
      PostgresAtomicOperationStore store, KafkaEventPublisher publisher,
      Function<EventEnvelopeV1, String> topicForEvent) {
    this.store = store; this.publisher = publisher; this.topicForEvent = topicForEvent;
  }

  /**
   * Publish one leased batch. A broker failure releases the current lease and
   * propagates the failure; retry cadence belongs to the hosting application.
   */
  public int publishPending(String workerId, int limit, int leaseSeconds) {
    List<LeasedOutboxEvent> events = store.claimOutbox(workerId, limit, leaseSeconds);
    int published = 0;
    for (LeasedOutboxEvent leased : events) {
      try {
        String topic = topicForEvent.apply(leased.event());
        if (topic == null || topic.isBlank()) throw new IllegalArgumentException("No topic configured for " + leased.event().eventType());
        publisher.publish(topic, leased.event()).get();
        if (!store.markPublished(leased.eventId(), workerId)) {
          throw new AtomicStoreUnavailableException("Outbox lease was lost before publication acknowledgement", null);
        }
        published++;
      } catch (RuntimeException error) {
        store.releaseOutboxLease(leased.eventId(), workerId);
        throw error;
      } catch (Exception error) {
        store.releaseOutboxLease(leased.eventId(), workerId);
        throw new AtomicStoreUnavailableException("Outbox publication failed", error);
      }
    }
    return published;
  }
}
