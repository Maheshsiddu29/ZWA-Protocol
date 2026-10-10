# zwa-lineage

Production verifier for frozen ZWA rooted-lineage evidence: relation `NormalLineageStepV2`, composed by Design A. Design: [`docs/lineage-integration.md`](../docs/lineage-integration.md). Contract: `ZWA-Lineage-Research/post-h1f/engineering-baseline/ENGINEERING_LINEAGE_CONTRACT.md`.

**Phase 2 snapshot path.** `phase2::Phase2LineageVerifier` loads pinned A4, A7, CPA, B8 or CKN views, applies verifier-owned provenance and finality policy, and authenticates chain context, issuance root and ZSA Action terminal. Container v3 carries the two fixed Layer-B evidence blobs. The Phase 1 `Phase1LineageService` and v2 container remain available for regression and return `PENDING_AUTHENTICATION` in rooted mode.

This is a separate Cargo workspace. It needs, next to this repository:

- `../../ZWA-Lineage-Research` (frozen R2B-L1 crate; Layer-B and R3-E research crates are test-only oracles);
- `../../zsa-feasibility/.cargo-home` (pinned offline cargo home).

```sh
lineage/scripts/check.sh   # fixture pins, fmt, clippy -D warnings, tests, lock unchanged
# To regenerate the classified Layer-B matrix alongside the full check:
LAYER_B_EVIDENCE_DIR="$PWD/lineage/evidence/phase-2" lineage/scripts/check.sh /tmp/zwa-lineage-check-evidence
```

| Path | Content |
|---|---|
| `src/` | domain model, container v2, container v3, frozen verifier adapter, Design A, Layer-B snapshot adapters, orchestration |
| `vendor/layer-b-core/` | pinned local extraction of frozen Layer-B verification modules; no RPC or HTTP dependency |
| `snapshots/` | pinned, immutable A4, A7, CPA, B8 and CKN research views; load checks SHA-256 and re-derives chain semantics |
| `tests/fixtures/` | frozen research step proofs and public tuples (sha256-pinned copies; no witnesses) |
| `tests/golden.rs`, `tests/adversarial.rs`, `tests/differential.rs` | N = 3 and CKN fixtures; negative cases; comparison with the frozen research verifier |
| `examples/` | `frozen_identity` (VK and parameter identity), `measure_n3` (timings) |
| `evidence/phase-1/` | Phase 1 validation record |
| `evidence/phase-2/` | Phase 2 differential classification, dependency isolation, and release evidence |

The snapshot adapter links the repository-local `zwa-layer-b-core` package. Its source manifest and `scripts/verify-layer-b-core-source.sh` prove that the compiled verification code comes from research HEAD `3e7115d` without its RPC/capture functions. The frozen R2B-L1 proof crate remains a sibling build dependency, so the full lineage build is not yet standalone. No mainnet or generic node integration is claimed.
