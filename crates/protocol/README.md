# `zwa-protocol`

Canonical ZWA Protocol types, validation, and state rules. This crate is the single source of truth for the frozen Phase 0F/0G contract: canonical `AssetBase` and raw Orchard receiver byte types, BN254 field values, 64-bit amount/nonce/expiry types, `TradeIntent`, typed protocol errors, signed-root envelope payloads, proof-verification interfaces, and the matcher replay state model.

It contains no networking, persistence, transaction construction, or root signature verification. Root envelopes carry opaque signature material because no root signature algorithm has been approved. The Poseidon staging of `TradeCommitmentV1` lives in `zwa-commitments`.
