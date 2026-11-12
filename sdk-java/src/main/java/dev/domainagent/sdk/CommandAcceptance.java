package dev.domainagent.sdk;

/** Durable command-ingress outcome, distinct from the eventual business result. */
public enum CommandAcceptance {
  ACCEPTED,
  DUPLICATE
}
