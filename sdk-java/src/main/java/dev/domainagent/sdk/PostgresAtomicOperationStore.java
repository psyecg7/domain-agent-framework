package dev.domainagent.sdk;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.time.Instant;
import java.sql.Timestamp;
import java.util.ArrayList;
import java.util.List;
import javax.sql.DataSource;

/**
 * PostgreSQL operation claim plus domain mutation plus outbox insert in one transaction.
 *
 * The supplied mutation may issue only SQL through the supplied connection. Broker,
 * payment, device, and HTTP calls cannot be part of this transaction.
 */
public final class PostgresAtomicOperationStore {
  private final DataSource dataSource;
  private final String operationsTable;
  private final String outboxTable;
  private final EventEnvelopeCodec codec;

  public PostgresAtomicOperationStore(DataSource dataSource, String tablePrefix, EventEnvelopeCodec codec) {
    if (dataSource == null) throw new IllegalArgumentException("dataSource is required");
    if (tablePrefix == null || !tablePrefix.matches("[A-Za-z_][A-Za-z0-9_]*")) {
      throw new IllegalArgumentException("tablePrefix must be a SQL identifier");
    }
    this.dataSource = dataSource;
    this.operationsTable = tablePrefix + "_operations";
    this.outboxTable = tablePrefix + "_outbox";
    this.codec = codec;
  }

  /** Run once during deployment, not from every application replica at startup. */
  public void migrate() {
    try (Connection connection = dataSource.getConnection(); Statement statement = connection.createStatement()) {
      statement.executeUpdate("CREATE TABLE IF NOT EXISTS " + operationsTable + " ("
          + "operation_id VARCHAR(255) PRIMARY KEY, outcome VARCHAR(255), outbox_event_id VARCHAR(255), completed_at TIMESTAMPTZ)");
      statement.executeUpdate("CREATE TABLE IF NOT EXISTS " + outboxTable + " ("
          + "event_id VARCHAR(255) PRIMARY KEY, operation_id VARCHAR(255) UNIQUE NOT NULL, event_json TEXT NOT NULL, "
          + "status VARCHAR(16) NOT NULL, published_at TIMESTAMPTZ, lease_owner VARCHAR(255), lease_expires_at TIMESTAMPTZ, publish_attempts INTEGER NOT NULL DEFAULT 0)");
    } catch (SQLException error) {
      throw new AtomicStoreUnavailableException("Atomic operation migration failed", error);
    }
  }

  public AtomicOperationResult executeOnce(String operationId, SqlMutation mutation, EventEnvelopeV1 outboxEvent) {
    required(operationId, "operationId");
    if (mutation == null) throw new IllegalArgumentException("mutation is required");
    try (Connection connection = dataSource.getConnection()) {
      boolean previousAutoCommit = connection.getAutoCommit();
      connection.setAutoCommit(false);
      try {
        if (!claim(connection, operationId)) {
          AtomicOperationResult existing = existing(connection, operationId);
          connection.commit();
          return existing;
        }
        String outcome = mutation.apply(connection);
        required(outcome, "outcome");
        String eventId = null;
        if (outboxEvent != null) {
          eventId = outboxEvent.eventId();
          insertOutbox(connection, operationId, outboxEvent);
        }
        try (PreparedStatement update = connection.prepareStatement(
            "UPDATE " + operationsTable + " SET outcome = ?, outbox_event_id = ?, completed_at = ? WHERE operation_id = ?")) {
          update.setString(1, outcome); update.setString(2, eventId); update.setObject(3, Instant.now()); update.setString(4, operationId);
          update.executeUpdate();
        }
        connection.commit();
        return new AtomicOperationResult(operationId, outcome, false, eventId);
      } catch (SQLException | RuntimeException error) {
        connection.rollback();
        throw error;
      } finally {
        connection.setAutoCommit(previousAutoCommit);
      }
    } catch (SQLException error) {
      throw new AtomicStoreUnavailableException("Atomic operation storage is unavailable", error);
    }
  }

  private boolean claim(Connection connection, String operationId) throws SQLException {
    try (PreparedStatement insert = connection.prepareStatement(
        "INSERT INTO " + operationsTable + " (operation_id) VALUES (?) ON CONFLICT DO NOTHING")) {
      insert.setString(1, operationId);
      return insert.executeUpdate() == 1;
    }
  }

  private AtomicOperationResult existing(Connection connection, String operationId) throws SQLException {
    try (PreparedStatement query = connection.prepareStatement(
        "SELECT outcome, outbox_event_id FROM " + operationsTable + " WHERE operation_id = ?")) {
      query.setString(1, operationId);
      try (ResultSet row = query.executeQuery()) {
        if (!row.next() || row.getString("outcome") == null) {
          throw new AtomicStoreUnavailableException("Operation claim did not yield a committed outcome", null);
        }
        return new AtomicOperationResult(operationId, row.getString("outcome"), true, row.getString("outbox_event_id"));
      }
    }
  }

  private void insertOutbox(Connection connection, String operationId, EventEnvelopeV1 event) throws SQLException {
    try (PreparedStatement insert = connection.prepareStatement(
        "INSERT INTO " + outboxTable + " (event_id, operation_id, event_json, status) VALUES (?, ?, ?, 'PENDING')")) {
      insert.setString(1, event.eventId()); insert.setString(2, operationId); insert.setString(3, codec.encode(event));
      insert.executeUpdate();
    }
  }

  /** Atomically lease a disjoint batch. A crashed worker's lease expires. */
  public List<LeasedOutboxEvent> claimOutbox(String workerId, int limit, int leaseSeconds) {
    required(workerId, "workerId");
    if (limit < 1 || leaseSeconds < 1) throw new IllegalArgumentException("limit and leaseSeconds must be positive");
    try (Connection connection = dataSource.getConnection()) {
      connection.setAutoCommit(false);
      try {
        List<LeasedOutboxEvent> leased = new ArrayList<>();
        String select = "SELECT event_id, operation_id, event_json FROM " + outboxTable
            + " WHERE status = 'PENDING' OR (status = 'LEASED' AND lease_expires_at < CURRENT_TIMESTAMP)"
            + " ORDER BY event_id FOR UPDATE SKIP LOCKED LIMIT ?";
        try (PreparedStatement query = connection.prepareStatement(select)) {
          query.setInt(1, limit);
          try (ResultSet rows = query.executeQuery()) {
            while (rows.next()) {
              String eventId = rows.getString("event_id");
              try (PreparedStatement update = connection.prepareStatement(
                  "UPDATE " + outboxTable + " SET status = 'LEASED', lease_owner = ?, lease_expires_at = ?, publish_attempts = publish_attempts + 1 WHERE event_id = ?")) {
                update.setString(1, workerId);
                update.setTimestamp(2, Timestamp.from(Instant.now().plusSeconds(leaseSeconds)));
                update.setString(3, eventId);
                update.executeUpdate();
              }
              leased.add(new LeasedOutboxEvent(eventId, rows.getString("operation_id"), codec.decode(rows.getString("event_json")), workerId));
            }
          }
        }
        connection.commit();
        return leased;
      } catch (SQLException | RuntimeException error) {
        connection.rollback();
        throw error;
      }
    } catch (SQLException error) {
      throw new AtomicStoreUnavailableException("Outbox lease storage is unavailable", error);
    }
  }

  /** Mark broker acceptance after a successful publish. Repeating it is harmless. */
  public boolean markPublished(String eventId, String workerId) {
    required(eventId, "eventId"); required(workerId, "workerId");
    return updateLease(eventId, workerId,
        "status = 'PUBLISHED', published_at = CURRENT_TIMESTAMP, lease_owner = NULL, lease_expires_at = NULL");
  }

  /** Make a failed publication immediately available to another worker. */
  public boolean releaseOutboxLease(String eventId, String workerId) {
    required(eventId, "eventId"); required(workerId, "workerId");
    return updateLease(eventId, workerId, "status = 'PENDING', lease_owner = NULL, lease_expires_at = NULL");
  }

  private boolean updateLease(String eventId, String workerId, String values) {
    try (Connection connection = dataSource.getConnection(); PreparedStatement update = connection.prepareStatement(
        "UPDATE " + outboxTable + " SET " + values + " WHERE event_id = ? AND status = 'LEASED' AND lease_owner = ?")) {
      update.setString(1, eventId); update.setString(2, workerId);
      return update.executeUpdate() == 1;
    } catch (SQLException error) {
      throw new AtomicStoreUnavailableException("Outbox storage is unavailable", error);
    }
  }

  private static void required(String value, String name) {
    if (value == null || value.isBlank()) throw new IllegalArgumentException(name + " is required");
  }
}
