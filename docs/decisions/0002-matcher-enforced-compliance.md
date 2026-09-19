# ADR 0002: Matcher-enforced compliance

- **Status:** Accepted for MVP
- **Decision:** Enforce issuer-root authentication, credential-root authentication, proof pairing, root freshness, replay protection, recipient-control authentication, fee policy, and the allow/block decision in the matcher/application layer.

## Context

The experimental Zcash/ZSA consensus validates shielded ownership, value conservation, nullifiers, proofs, and transaction atomicity. It does not understand ZWA issuer roots, credentials, or transfer policy.

## Rationale

The matcher can verify the two ZK proofs against the same canonical trade, apply signed-root and freshness policy, and only then construct settlement. This approach matches what Phase 0 demonstrated without modifying Zcash consensus.

## Consequences

Compliance is not a consensus property. A malicious or bypassed matcher can omit ZWA policy, and direct ZSA transfers outside ZWA remain possible. Matcher code, configuration, durable replay state, privacy controls, and auditability are security-critical. The matcher still cannot forge note ownership or make a consensus-invalid transaction valid.
