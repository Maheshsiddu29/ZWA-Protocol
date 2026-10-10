# Production ZWA integration — Phase 2 closure

## Verdict and scope

**PRODUCTION ZWA INTEGRATION PHASE 2 COMPLETE.** Layer-B rooted verification is integrated through the repository-local `zwa-layer-b-core`; network/RPC tooling is outside the production verifier boundary; the matcher permits execution only for `VERIFIED`; the frozen research differential matrix is fully accounted for; and the frozen research baseline is unchanged. This is the Phase-2 verifier release gate, not a claim of a self-contained deployable package or public-network support. Phase 3 has not begun.

The prior `INCOMPLETE` result is preserved in `phase-2-initial-result.json`. The original stop was the normal dependency path `zwa-lineage → zwa-layer-b → reqwest 0.11.27 → hyper 0.14.32 → h2 0.3.27` with RUSTSEC-2026-0258. `PHASE_2_DEPENDENCY_ISOLATION.md` records the module boundary, provenance, matrix, and measured result. This closure was performed from `integration/lineage-phase-2` at `99b2b8dd152a485d50a11b8e662e5cc7cdd72f10`; all new commits are local.

## Architecture, provenance, and security

`LineageVerificationService` routes through chain context, root, and terminal authenticators into the imported core. The core consumes pinned, authenticated snapshot bytes. The materializer and `SOURCE_MANIFEST.json` pin seven frozen source modules to research HEAD `3e7115d0e4c2f1cf36355f028447fc1b58290583`; three complete modules are byte-identical, while four are deterministically extracted with verification-function text preserved. `scripts/verify-layer-b-core-source.sh` verifies hashes and rejects network-client symbols. The frozen research checkout was not edited.

The normal `zwa-lineage` Cargo graph has no `reqwest`, `hyper`, or `h2`; `dependency-tree.txt` contains the reverse and forward trees. Frozen Layer-B remains a **dev dependency** for differential tests and retains the advisory in its research/capture graph. The lockfile therefore still mentions `h2`. The pinned offline Cargo home has no advisory database, so raw `cargo audit` was unavailable; `cargo-audit.txt` records the failed attempt. The security conclusion is limited to the production **normal dependency graph** and source boundary.

The frozen R2B-L1 proof crate remains a normal sibling **build/source** dependency; the pinned offline Cargo home is also required. **Standalone production lineage build: NOT COMPLETE.** No runtime verifier call reads the sibling checkout or uses its RPC helper.

## Differential equivalence and rooted matrix

`layer-b-case-manifest.json` classifies **236** authoritative rows: 218 frozen Layer-B vectors and 18 H1-rooted key rows. **202** are applicable: **196** direct production-core replays and **6** rooted key rows mapped to existing production decisions. Outcomes are **201 AGREES**, **1 INTENTIONAL_STRICTER_REJECTION**, **0 DIVERGES**. The other **34 NOT_PORTABLE** rows each have a reason; **0 MISSING_AUTHORITATIVE_INPUT** rows remain in the enumerated research records. The replay test compares the imported core to frozen ChainRef, root, terminal, combined, and snapshot-loader outputs. The existing 20 application-level differential cases also pass. The single stricter case, `CK-OBJ-CP`, is a policy choice: production refuses CKN checkpoint inheritance and denies trade although frozen research can accept CP under explicit trust. No production acceptance replaces a frozen rejection.

The primary request and gate regressions pass:

- N=3 on A7: `VERIFIED`, trade permitted.
- N=3 on A4: `PENDING_FINALITY`, trade denied.
- CKN: `CHECKPOINT_TRUST_REQUIRED`, trade denied.
- Corrupted root, terminal, wrong Orchard root, broken Design-A proof, and broken join: `REJECTED`, trade denied.
- Accepted-reference reorg: `INVALIDATED_BY_REORG`, trade denied.
- Finalized-reference contradiction: `INVALIDATED_BY_REORG` with `FINALITY_VIOLATION`, trade denied.
- Every tested non-`VERIFIED` status: trade denied.

## Fixture gaps and explicit test scope

**Invalid issued-rho — AUTHORITATIVE END-TO-END FIXTURE: NOT AVAILABLE.** The frozen C5a bad-rho block did not become a committed authenticated view. A full mirrored Layer-B tamper row exercises `ROOT_ISSUANCE_INVALID`; a separate production A7 test corrupts the public issuance claim and proves `REJECTED` plus trade denied. **PRODUCTION NEGATIVE TEST; NOT BAD-ISSUED-RHO RESEARCH DIFFERENTIAL EVIDENCE.** It is a root-boundary fail-closed test, not reproduction of the C5a consensus observation.

**Vanilla/non-ZSA terminal — AUTHORITATIVE LAYER-B TM END-TO-END FIXTURE: NOT AVAILABLE.** A production core-boundary test loads the SHA-256-pinned frozen A2b-V V5 view, authenticates a vanilla Action, and gets `TERM_NOT_ZSA_ACTION`; the adapter classifies that code as `UNSUPPORTED` and the matcher denies trade. **PRODUCTION NEGATIVE TEST; NOT A LAYER-B RESEARCH DIFFERENTIAL CASE.** V5 is outside the five production-pinned Phase-2 views.

## Validation and performance

- `lineage/scripts/check.sh` with separate output directories for legacy and full-matrix evidence: fixture pins, provenance, `cargo fmt --check`, `cargo clippy --offline --locked --all-targets -- -D warnings`, `cargo test --offline --locked`, classified matrix builder, and unchanged lockfile: **PASS**.
- Phase-1 adversarial, container v2/v3, snapshot-integrity, rooted, finality, and matcher-gate tests: **PASS** within the lineage test run.
- Protocol core `cargo fmt --check`, `cargo clippy --offline --locked --workspace -- -D warnings`, and `cargo test --offline --locked --workspace`: **PASS** (95 tests); `npm test`: **PASS** (15 tests).
- Production `cargo tree --offline --locked --edges normal -i h2`, `-i reqwest`, and `-i hyper`: **none reachable**. Source/provenance and secret-pattern scans: **PASS**.
- Research HEAD stayed `3e7115d0e4c2f1cf36355f028447fc1b58290583`; tracked count stayed **1,699**; aggregate baseline hash before and after stayed `7c05d1e9b3884e30fc901535f0d8ca95f9430204a379f0182c248416760ca4bc`.

Ten release-mode N=3 A7 runs averaged **22.64 ms** total versus the prior **22.33 ms** (about 1.4% higher), and **11.88 ms** Design A versus **11.39 ms**. This local measurement shows no meaningful regression; it is not a formal performance guarantee.

## Remaining limits

Independent human review is pending. The verifier uses fixed regtest snapshots and an in-memory finality tracker; tracker observations do not survive process restart. R2B-L1 and the offline Cargo home still prevent standalone build packaging. Frozen research/capture tooling still retains the advisory, and raw Cargo audit needs a local advisory database. These are explicit limits to deployment and do not widen the Phase-2 verifier claim.

**READY FOR PHASE 3 after user review.** No push, merge, PR, release, or Phase-3 work was performed.
