package dev.domainagent.sdk;

import dev.domainagent.sdk.grpc.InventoryCommand;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import javax.sql.DataSource;

/**
 * PostgreSQL command acceptance and multi-worker inbox leasing for Inventory.
 *
 * <p>The gRPC service sends {@code ACCEPTED} only after {@link #accept} commits.
 * The Inventory domain must still atomically combine its eventual business
 * mutation, operation evidence, and result outbox in its own transaction.</p>
 */
public final class PostgresInventoryCommandInbox implements InventoryCommandInbox {
  private final DataSource dataSource;
  private final String table;

  public PostgresInventoryCommandInbox(DataSource dataSource, String tableName) {
    if (dataSource == null) throw new IllegalArgumentException("dataSource is required");
    if (tableName == null || !tableName.matches("[A-Za-z_][A-Za-z0-9_]*")) {
      throw new IllegalArgumentException("tableName must be a SQL identifier");
    }
    this.dataSource = dataSource;
    this.table = tableName;
  }

  /** Run through the controlled deployment migration path. */
  public void migrate() {
    try (Connection connection = dataSource.getConnection(); Statement statement = connection.createStatement()) {
      statement.executeUpdate("CREATE TABLE IF NOT EXISTS " + table + " ("
          + "operation_id VARCHAR(255) NOT NULL, attempt_id VARCHAR(255) NOT NULL, "
          + "event_id VARCHAR(255) NOT NULL UNIQUE, command_bytes BYTEA NOT NULL, "
          + "status VARCHAR(16) NOT NULL, lease_owner VARCHAR(255), lease_expires_at TIMESTAMPTZ, "
          + "accepted_at TIMESTAMPTZ NOT NULL, PRIMARY KEY(operation_id, attempt_id))");
    } catch (SQLException error) {
      throw new InventoryCommandInboxUnavailableException("Inventory command inbox migration failed", error);
    }
  }

  @Override
  public CommandAcceptance accept(InventoryCommand command) {
    validate(command);
    byte[] bytes = command.toByteArray();
    try (Connection connection = dataSource.getConnection()) {
      connection.setAutoCommit(false);
      try {
        if (insert(connection, command, bytes)) {
          connection.commit();
          return CommandAcceptance.ACCEPTED;
        }
        byte[] existing = existingBytes(connection, command);
        connection.commit();
        if (Arrays.equals(existing, bytes)) return CommandAcceptance.DUPLICATE;
        throw new IllegalArgumentException("operation_id and attempt_id already identify a different command");
      } catch (SQLException | RuntimeException error) {
        connection.rollback();
        throw error;
      }
    } catch (SQLException error) {
      throw new InventoryCommandInboxUnavailableException("Inventory command inbox could not record acceptance", error);
    }
  }

  /** Claim a disjoint batch. Expired leases become eligible after a worker crash. */
  public List<InventoryCommand> claimPending(String workerId, int limit, int leaseSeconds) {
    required(workerId, "workerId");
    if (limit < 1 || leaseSeconds < 1) throw new IllegalArgumentException("limit and leaseSeconds must be positive");
    try (Connection connection = dataSource.getConnection()) {
      connection.setAutoCommit(false);
      try {
        List<InventoryCommand> claimed = new ArrayList<>();
        String select = "SELECT operation_id, attempt_id, command_bytes FROM " + table
            + " WHERE status = 'PENDING' OR (status = 'LEASED' AND lease_expires_at < CURRENT_TIMESTAMP)"
            + " ORDER BY accepted_at FOR UPDATE SKIP LOCKED LIMIT ?";
        try (PreparedStatement query = connection.prepareStatement(select)) {
          query.setInt(1, limit);
          try (ResultSet rows = query.executeQuery()) {
            while (rows.next()) {
              String operationId = rows.getString("operation_id");
              String attemptId = rows.getString("attempt_id");
              try (PreparedStatement lease = connection.prepareStatement(
                  "UPDATE " + table + " SET status = 'LEASED', lease_owner = ?, lease_expires_at = ? "
                      + "WHERE operation_id = ? AND attempt_id = ?")) {
                lease.setString(1, workerId);
                lease.setTimestamp(2, Timestamp.from(Instant.now().plusSeconds(leaseSeconds)));
                lease.setString(3, operationId);
                lease.setString(4, attemptId);
                lease.executeUpdate();
              }
              claimed.add(InventoryCommand.parseFrom(rows.getBytes("command_bytes")));
            }
          }
        }
        connection.commit();
        return claimed;
      } catch (Exception error) {
        connection.rollback();
        if (error instanceof RuntimeException runtime) throw runtime;
        throw new InventoryCommandInboxUnavailableException("Inventory command inbox could not claim work", error);
      }
    } catch (SQLException error) {
      throw new InventoryCommandInboxUnavailableException("Inventory command inbox could not claim work", error);
    }
  }

  public boolean markCompleted(InventoryCommand command, String workerId) {
    return transition(command, workerId, "COMPLETED");
  }

  public boolean releasePending(InventoryCommand command, String workerId) {
    return transition(command, workerId, "PENDING");
  }

  private boolean insert(Connection connection, InventoryCommand command, byte[] bytes) throws SQLException {
    try (PreparedStatement insert = connection.prepareStatement(
        "INSERT INTO " + table + " (operation_id, attempt_id, event_id, command_bytes, status, accepted_at) "
            + "VALUES (?, ?, ?, ?, 'PENDING', ?) ON CONFLICT (operation_id, attempt_id) DO NOTHING")) {
      insert.setString(1, command.getOperationId());
      insert.setString(2, command.getAttemptId());
      insert.setString(3, command.getEventId());
      insert.setBytes(4, bytes);
      insert.setTimestamp(5, Timestamp.from(Instant.now()));
      return insert.executeUpdate() == 1;
    }
  }

  private byte[] existingBytes(Connection connection, InventoryCommand command) throws SQLException {
    try (PreparedStatement query = connection.prepareStatement(
        "SELECT command_bytes FROM " + table + " WHERE operation_id = ? AND attempt_id = ?")) {
      query.setString(1, command.getOperationId());
      query.setString(2, command.getAttemptId());
      try (ResultSet row = query.executeQuery()) {
        if (!row.next()) throw new InventoryCommandInboxUnavailableException("Command identity disappeared during acceptance", null);
        return row.getBytes("command_bytes");
      }
    }
  }

  private boolean transition(InventoryCommand command, String workerId, String nextStatus) {
    required(workerId, "workerId");
    try (Connection connection = dataSource.getConnection(); PreparedStatement update = connection.prepareStatement(
        "UPDATE " + table + " SET status = ?, lease_owner = NULL, lease_expires_at = NULL "
            + "WHERE operation_id = ? AND attempt_id = ? AND status = 'LEASED' AND lease_owner = ?")) {
      update.setString(1, nextStatus);
      update.setString(2, command.getOperationId());
      update.setString(3, command.getAttemptId());
      update.setString(4, workerId);
      return update.executeUpdate() == 1;
    } catch (SQLException error) {
      throw new InventoryCommandInboxUnavailableException("Inventory command inbox could not change command status", error);
    }
  }

  private static void validate(InventoryCommand command) {
    if (command == null) throw new IllegalArgumentException("command is required");
    required(command.getOperationId(), "operationId");
    required(command.getAttemptId(), "attemptId");
    required(command.getEventId(), "eventId");
  }

  private static void required(String value, String name) {
    if (value == null || value.isBlank()) throw new IllegalArgumentException(name + " is required");
  }
}
