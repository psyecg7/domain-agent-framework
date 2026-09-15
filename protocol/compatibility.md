# Protocol compatibility

Protocol versions are independent of Python package versions.

- A producer must emit exactly one declared protocol version.
- A consumer may support multiple declared versions during a migration.
- An absent event version is accepted only as the documented pre-v1 legacy
  event shape; new producers must emit `protocol_version: 1`.
- Adding a required field, changing a field type, changing a signed byte
  representation, or changing a domain outcome meaning requires a new version.
- Domain payload schemas are versioned by the domain that owns the event type.
- Every implementation must run the fixed vectors for each version it claims.

Do not use a generic "best effort" decoder for an unknown version. Reject it,
retain the original message for diagnosis, and upgrade deliberately.
