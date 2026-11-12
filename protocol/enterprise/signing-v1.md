# Enterprise signing v1

The Policy service signs the UTF-8 bytes of the `claims` object only. The
outer `{ "claims": ..., "signature": ... }` envelope is not signed.

## Canonical JSON profile

Version 1 uses a deliberately narrow canonical JSON profile so independent
implementations can produce identical bytes:

- objects have string keys, sorted lexicographically by Unicode code point;
- output is UTF-8 with no insignificant whitespace;
- strings use ordinary JSON escaping for quotation marks, backslashes, and
  U+0000 through U+001F; non-ASCII characters remain UTF-8, not `\\u` escapes;
- allowed values are objects, arrays, strings, booleans, null, and integers;
- floating-point numbers are not portable in v1 signed parameters or
  preconditions. Represent a decimal as a string or an integer minor unit.

This is the exact profile implemented by the current Python reference for the
portable value set. Do not substitute a serializer that escapes all non-ASCII
characters or emits a different number format. A later protocol version may
adopt a broader standard canonicalization scheme; it must not change v1.

`parameters_sha256` and `preconditions_sha256` are lowercase SHA-256 hashes of
their respective objects encoded with this same profile. `claims` repeats those
hashes and is signed with Ed25519. The signature is standard URL-safe Base64
text, including any `=` padding produced by the signer.

## Verification order

1. Decode the command and authorization schemas.
2. Recompute both object hashes and compare them to the command and claims.
3. Canonically encode claims and verify Ed25519 with the trusted public key
   selected by `key_id`.
4. Check issuer policy, audience, time window, action ownership, revocation,
   and an atomic replay claim before the effect.
5. Pass signed `preconditions` to the target domain, which performs its own
   native atomic conditional write and returns a business `CONFLICT` if stale.

Run the [fixed v1 vector](../test-vectors/enterprise/authorization-v1.json)
before claiming interoperability.
