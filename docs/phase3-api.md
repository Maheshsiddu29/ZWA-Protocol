# Phase 3 rooted-lineage API

This Rust process serves the provisional **regtest fixture-backed** rooted verifier. It verifies actual `NormalLineageStepV2`/Design-A proofs, authenticated issuance and terminal evidence, ChainRef history, and K=3 policy finality. It does not observe a live node. Independent human review remains **PENDING**.

## Build and start

The production source tree contains frozen R2B-L1 source in `lineage/vendor/r2b-l1/` and the pinned Layer-B core in `lineage/vendor/layer-b-core/`. `lineage/scripts/verify-r2b-l1-source.py` and `lineage/scripts/verify-layer-b-core-source.sh` check hashes. Source in R2B-L1 is byte-identical to research except seven `#[path]` strings, which now point at byte-identical packaged Orchard cf801a5 files. The local package name is distinct from the original research oracle package so differential tests can link both. No circuit formula, witness mapping, generator, VK, parameter, transcript, or proof encoding was changed. Research HEAD: `3e7115d0e4c2f1cf36355f028447fc1b58290583`.

The lockfile pins registry versions and Git revisions. For a machine with network access, prepare a cache with `cd lineage && CARGO_NET_OFFLINE=false cargo fetch --locked`; historical upstream Git access is required. The checked local offline build used `CARGO_HOME=../../zsa-feasibility/.cargo-home` because that cache was already populated. For a copied checkout without any sibling research source, use the same prepared `CARGO_HOME` and:

```sh
cd lineage
python3 scripts/verify-r2b-l1-source.py
scripts/verify-layer-b-core-source.sh
CARGO_HOME=/absolute/path/to/prepared/cargo-home cargo build --offline --locked --workspace
```

`lineage/scripts/check.sh` runs production source pins, fixtures, formatting, clippy, and tests with a prepared cache. `lineage/scripts/check-research.sh` additionally runs the full frozen differential harness and 236-row classification when the exact sibling research checkout is present. The research harness is isolated under `lineage/research-tests/`; it is not in the production Cargo graph. Its frozen oracle still carries the machine-specific Orchard path from research. A clean CI runner must supply the pinned Orchard checkout at that path for the research-only job; this does not affect the production build.

Start the API with an explicit pinned view:

```sh
cd lineage
CARGO_HOME=/absolute/path/to/prepared/cargo-home cargo build --offline --locked -p zwa-lineage-api
ZWA_CHAIN_VIEW=A7 ZWA_BIND=127.0.0.1:3000 ./target/debug/zwa-lineage-api
```

`ZWA_CHAIN_VIEW` is required (`A4`, `A7`, `CPA`, `B8`, `CKN`). These are authenticated pinned research views, not live mainnet. Other configuration: `ZWA_BIND` defaults to `127.0.0.1:3000`; `ZWA_DEMO_ENABLED` defaults false; `ZWA_MAX_CONCURRENT` defaults 2 (1–16); `ZWA_MAX_BODY_BYTES` defaults 7,200,000 (25,000–16,000,000); `ZWA_LOG_LEVEL` is `info` or `off`. No CORS is enabled. Startup fails if source, fixture, snapshot, policy, VK, parameter, or configuration checks fail; `/health/ready` is served only after successful initialization. The source files are part of the application deployment because startup checks their hashes.

Demo mode is explicit:

```sh
ZWA_CHAIN_VIEW=A7 ZWA_DEMO_ENABLED=true ./target/debug/zwa-lineage-api
curl -s http://127.0.0.1:3000/v1/demo/scenarios
curl -s -H 'content-type: application/json' -d '{"scenarioId":"valid-rooted-n3"}' http://127.0.0.1:3000/v1/demo/run
```

Demo calls use fixed operator-owned A7, A4, CKN, and B8 verifier instances. The request cannot select a snapshot or enable checkpoint trust. Every observed verdict comes from a new real verifier call. Expected status is regression metadata. The reorg scenario observes A7 at startup and then switches that isolated tracker to B8. On non-demo services, both demo routes return 404.

## HTTP contract

All JSON POSTs require `content-type: application/json`. Unknown fields and malformed JSON are rejected. The evidence envelope accepts only canonical padded standard base64. Its decoded bytes must be the canonical rooted container **v3**; `1 ≤ N ≤ 1024`, exactly 5,120 bytes per proof, and no trailing bytes. The maximum v3 evidence size is 5,345,661 bytes. CPU work runs on a bounded blocking pool behind a semaphore. A permit wait times out after 30 seconds.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health/live` | Process responds |
| GET | `/health/ready` | Verifier initialized with pinned sources and views |
| GET | `/v1/verifier/info` | Actual public identity, policy, version, and snapshot |
| POST | `/v1/lineage/verify` | Run rooted verification against configured view |
| POST | `/v1/trade/authorize` | Re-run verifier and Phase 2 `TradePermission` gate |
| GET | `/v1/demo/scenarios` | List fixed fixture scenarios, only in demo mode |
| POST | `/v1/demo/run` | Execute one fixed fixture scenario, only in demo mode |

Request for verification and authorization:

```json
{"evidenceEncoding":"base64","evidence":"<canonical v3 bytes>"}
```

A real fixture request can be made without a made-up proof:

```sh
EVIDENCE=$(base64 < lineage/tests/fixtures/phase2/n3-v3.bin | tr -d '\n')
curl -s -H 'content-type: application/json' -d "{\"evidenceEncoding\":\"base64\",\"evidence\":\"$EVIDENCE\"}" http://127.0.0.1:3000/v1/lineage/verify
curl -s -H 'content-type: application/json' -d "{\"evidenceEncoding\":\"base64\",\"evidence\":\"$EVIDENCE\"}" http://127.0.0.1:3000/v1/trade/authorize
```

The response contains `verificationId` (a domain-separated SHA-256 of canonical bytes, **not** an authorization token), the existing Phase 2 `verification` result, a timeline of evaluated obligations, `lineagePermission`, `tradePermission`, `eligibility`, `settlement`, `actionEvaluated`, and `trustSource`. Exact examples from HTTP tests are in `lineage/evidence/phase-3/examples/`. `verification.status` is one of `VERIFIED`, `LINEAGE_PROOF_VALID`, `REJECTED`, `PENDING_AUTHENTICATION`, `PENDING_FINALITY`, `INVALIDATED_BY_REORG`, `CHECKPOINT_TRUST_REQUIRED`, `UNSUPPORTED`, `INTERNAL_ERROR`. Obligation statuses are `PASSED`, `FAILED`, `PENDING`, `NOT_EVALUATED`. The only allowed lineage status is `VERIFIED`.

For a valid request with a failed proof, HTTP returns 200 with `verification.status=REJECTED` and a failure code; a pending verdict is likewise a completed evaluation, not a transport failure. Input errors return JSON `{ "error": { "code": "...", "message": "..." } }` with 400, 413, or 415. Transport codes include `INVALID_JSON`, `INVALID_ENCODING`, `INVALID_BASE64`, `NONCANONICAL_BASE64`, `REQUEST_TOO_LARGE`, `EVIDENCE_TOO_LARGE`, `CONTENT_TYPE_REQUIRED`. `VERIFIER_BUSY`, `VERIFIER_UNAVAILABLE`, and `INTERNAL_ERROR` are server errors. Verification failure codes are the Phase 2 `verification.code` values; see the real examples rather than assuming a transport code for a rejected proof.

The `timeline` is derived from the returned obligation statuses. `verification.timing_us` exposes parse, Design-A, chain, root, terminal, finality, and total timings. Structured logs record the route, verification ID, snapshot, verdict, code, step count, and duration; raw evidence and witnesses are never logged.

## Trust boundary and restart

An authorized issuance root is authenticated against the pinned policy and view; it is separate from investor eligibility. Design A proves its public intermediate commitments/nullifiers and private relation steps, but does **not** hide the intermediate graph from the verifier. The API does not return private note openings, value, recipient keys, `rho`, `psi`, `rcm`, `nk`, or credential secrets. The public response intentionally exposes only statement/reference IDs, heights, statuses, and timing.

`lineagePermission=ALLOWED` means only that the rooted lineage gate passed in the current configured snapshot. `eligibility` is always `NOT_EVALUATED` because the protocol credential crate has no approved signature verifier bound to this action. `settlement` is always `NOT_EXECUTED`; there is no transaction broadcast or fund movement. A live execution path would need a fresh authorization check close to dispatch plus independent investor eligibility, recipient authorization, and settlement checks.

The finality tracker is in memory. It retains observations while one process remains alive, including the demo A7→B8 invalidation, but does not persist across restarts. After restart, the service reconstructs state from its configured authenticated pinned view; it cannot claim durable prior observation history. Checkpoint trust is off and cannot be enabled by requests. CKN returns `CHECKPOINT_TRUST_REQUIRED`. K=3 is an operator-owned policy, not a caller option.

## Validation

```sh
cd lineage
scripts/check.sh
scripts/check-research.sh                # exact research checkout required
CARGO_HOME=/absolute/path/to/prepared/cargo-home cargo build --offline --locked -p zwa-lineage-api
python3 apps/api/tests/http_integration.py  # requires localhost bind permission
```

The HTTP script starts real processes on loopback, tests both demo and non-demo modes, checks adversarial inputs and restart behavior, and writes observed JSON examples and a timing report. CI is in `.github/workflows/lineage-api.yml`; protocol-core CI remains separate.
