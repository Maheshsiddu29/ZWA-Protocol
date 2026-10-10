# zwa-lineage

Production verifier for frozen ZWA rooted-lineage evidence: relation `NormalLineageStepV2`, composed by Design A. Design: [`docs/lineage-integration.md`](../docs/lineage-integration.md). Contract: `ZWA-Lineage-Research/post-h1f/engineering-baseline/ENGINEERING_LINEAGE_CONTRACT.md`.

**Phase 1 status.** Design A runs the frozen R2B-L1 verifier under VK\* `a2b349d7…36b3`. Root, terminal and chain-context authentication are interfaces with `NotWired` adapters, so the rooted path returns `PENDING_AUTHENTICATION` and never `VERIFIED`.

This is a separate Cargo workspace. It needs, next to this repository:

- `../ZWA-Lineage-Research` (frozen R2B-L1 crate; research crates as test-only oracles);
- `../zsa-feasibility/.cargo-home` (pinned offline cargo home).

```sh
lineage/scripts/check.sh   # fixture pins, fmt, clippy -D warnings, tests, lock unchanged
```

| Path | Content |
|---|---|
| `src/` | domain model, container v2, frozen verifier adapter, Design A, authentication interfaces, orchestration |
| `tests/fixtures/` | frozen research step proofs and public tuples (sha256-pinned copies; no witnesses) |
| `tests/golden.rs`, `tests/adversarial.rs`, `tests/differential.rs` | N = 3 and CKN fixtures; negative cases; comparison with the frozen research verifier |
| `examples/` | `frozen_identity` (VK and parameter identity), `measure_n3` (timings) |
| `evidence/phase-1/` | Phase 1 validation record |
