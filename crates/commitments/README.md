# `zwa-commitments`

The canonical ZWA commitment engine and the only implementation of the frozen Phase 0F/0G encodings: the 32-byte `orchard::note::AssetBase` 128-bit limb split, the 43-byte raw Orchard receiver 128/128/88-bit limb split, the Poseidon domain separators, the Phase 0G recipient binding, and `TradeCommitmentV1`.

Poseidon is `light-poseidon`'s circom constructor over BN254. Its equality with `circomlib`/`circomlibjs` is asserted against Phase 0 vectors for every arity the circuits use rather than assumed. `tests/phase0_vectors.rs` reproduces both reference fixtures in `tests/fixtures/`, including the mandatory Phase 0F and Phase 0G golden `TradeCommitmentV1` values; expected values in these tests are authoritative and must never be edited to make a change pass.
