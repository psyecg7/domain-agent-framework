package dev.domainagent.sdk;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.time.Instant;
import javax.sql.DataSource;

/** Shared PostgreSQL replay claim for several Java Executor replicas. */
public final class PostgresReplayStore implements ReplayStore {
  private final DataSource dataSource;
  private final String table;

  public PostgresReplayStore(DataSource dataSource, String table) {
    if (table == null || !table.matches("[A-Za-z_][A-Za-z0-9_]*")) throw new IllegalArgumentException("table must be a SQL identifier");
    this.dataSource = dataSource; this.table = table;
  }

  /** Run during controlled deployment before accepting authorizations. */
  public void migrate() {
    try (Connection connection = dataSource.getConnection(); var statement = connection.createStatement()) {
      statement.executeUpdate("CREATE TABLE IF NOT EXISTS " + table + " (authorization_id VARCHAR(255) PRIMARY KEY, expires_at BIGINT NOT NULL)");
    } catch (SQLException error) {
      throw new AtomicStoreUnavailableException("Replay-store migration failed", error);
    }
  }

  @Override public boolean claim(String authorizationId, long expiresAtEpochSeconds) {
    try (Connection connection = dataSource.getConnection()) {
      try (PreparedStatement cleanup = connection.prepareStatement("DELETE FROM " + table + " WHERE expires_at < ?")) {
        cleanup.setLong(1, Instant.now().getEpochSecond()); cleanup.executeUpdate();
      }
      try (PreparedStatement claim = connection.prepareStatement(
          "INSERT INTO " + table + " (authorization_id, expires_at) VALUES (?, ?) ON CONFLICT DO NOTHING")) {
        claim.setString(1, authorizationId); claim.setLong(2, expiresAtEpochSeconds);
        return claim.executeUpdate() == 1;
      }
    } catch (SQLException error) {
      throw new AtomicStoreUnavailableException("Replay store is unavailable", error);
    }
  }
}
