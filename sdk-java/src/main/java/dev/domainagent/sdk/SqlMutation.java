package dev.domainagent.sdk;

import java.sql.Connection;
import java.sql.SQLException;

/** Domain SQL mutation that runs inside the operation/outbox transaction. */
@FunctionalInterface
public interface SqlMutation {
  String apply(Connection connection) throws SQLException;
}
