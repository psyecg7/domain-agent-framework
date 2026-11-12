package dev.domainagent.sdk;

import java.util.concurrent.CompletableFuture;
import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.clients.producer.RecordMetadata;

/** Kafka publisher for event-envelope v1; domain code chooses the topic. */
public final class KafkaEventPublisher implements AutoCloseable {
  private final KafkaProducer<String, String> producer;
  private final EventEnvelopeCodec codec;

  public KafkaEventPublisher(KafkaProducer<String, String> producer, EventEnvelopeCodec codec) {
    this.producer = producer;
    this.codec = codec;
  }

  public CompletableFuture<RecordMetadata> publish(String topic, EventEnvelopeV1 event) {
    if (topic == null || topic.isBlank()) throw new IllegalArgumentException("topic is required");
    String key = event.entityType() + ":" + event.entityId();
    ProducerRecord<String, String> record = new ProducerRecord<>(topic, key, codec.encode(event));
    CompletableFuture<RecordMetadata> result = new CompletableFuture<>();
    producer.send(record, (metadata, error) -> {
      if (error == null) result.complete(metadata);
      else result.completeExceptionally(error);
    });
    return result;
  }

  @Override public void close() { producer.close(); }
}
