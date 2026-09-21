# `zwa-protocol`

Canonical ZWA Protocol types, validation, and state rules. This crate is the single source of truth for the frozen Phase 0F/0G contract: canonical `AssetBase` and raw Orchard receiver byte types, BN254 field values, 64-bit amount/nonce/expiry types, `TradeIntent`, typed protocol errors, shared signed-root envelope primitives, and proof-verification interfaces.

Concrete issuer and credential envelopes live in `zwa-credentials`. Both crates carry opaque signature material because no root signature algorithm has been approved. Structural validation is not cryptographic authentication. The Poseidon staging of `TradeCommitmentV1` lives in `zwa-commitments`.

It contains no networking, persistence, transaction construction, or root signature verification.
