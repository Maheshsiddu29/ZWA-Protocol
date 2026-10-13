# zwa-lineage

Production verifier for frozen ZWA rooted-lineage evidence: relation `NormalLineageStepV2`, composed by Design A. Design: [`docs/lineage-integration.md`](../docs/lineage-integration.md). Contract: `ZWA-Lineage-Research/post-h1f/engineering-baseline/ENGINEERING_LINEAGE_CONTRACT.md`.

**Phase 2 snapshot path.** `phase2::Phase2LineageVerifier` loads pinned A4, A7, CPA, B8 or CKN views, applies verifier-owned provenance and finality policy, and authenticates chain context, issuance root and ZSA Action terminal. Container v3 carries the two fixed Layer-B evidence blobs. The Phase 1 `Phase1LineageService` and v2 container remain available for regression and return `PENDING_AUTHENTICATION` in rooted mode.

This is a separate Cargo workspace. Production verifier source is repository-local; external Cargo dependencies remain pinned by `Cargo.lock`. See [`docs/phase3-api.md`](../docs/phase3-api.md) for build, API, demo, and trust contracts.

The frozen research differential harness is under `research-tests/` and still requires the sibling research checkout. Ordinary production build and tests do not.
