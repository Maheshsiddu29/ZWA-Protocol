# Phase 3 API in Postman

This collection exercises the real rooted verifier with pinned, public regtest fixtures. It does not use a mock API or contain credentials, private note openings, or witness data. The local service evaluates lineage only: `eligibility` stays `NOT_EVALUATED` and `settlement` stays `NOT_EXECUTED`.

## Start the API

From the repository root:

```sh
cd lineage
CARGO_HOME=../../zsa-feasibility/.cargo-home cargo build --offline --locked -p zwa-lineage-api
ZWA_CHAIN_VIEW=A7 ZWA_DEMO_ENABLED=true ZWA_BIND=127.0.0.1:3000 ZWA_LOG_LEVEL=off ./target/debug/zwa-lineage-api
```

Keep that terminal open. The service takes about ten seconds to initialize its pinned sources, fixtures, snapshots, policy, VK, and parameters. Confirm `http://127.0.0.1:3000/health/ready` returns `status: READY`, `snapshot: A7`, and `trustSource: PINNED_REGTEST_FIXTURE`. Demo mode must be enabled for both `/v1/demo/*` routes. The configured chain view is an operator setting; requests cannot change it.

If the prepared Cargo cache is elsewhere, substitute its absolute path for `CARGO_HOME`. The checked local cache is the sibling `zsa-feasibility/.cargo-home` directory.

## Import and run

1. In Postman, **Import** `ZWA-Phase3.postman_collection.json`.
2. Import `ZWA-Local.postman_environment.json` the same way.
3. Select **ZWA Local Phase 3** in the environment picker. Its `base_url` is `http://127.0.0.1:3000`.
4. Open a request and click **Send**. Check the **Test Results** tab and the returned JSON.
5. To execute everything, open the collection menu, choose **Run collection**, keep all 36 requests selected, and click **Run**. The oversized request is last; its pre-request script creates a 7,200,002-byte body locally for the 413 check.

The folders are **01 Health**, **02 Verifier Information**, **03 Demo Scenarios**, **04 Rooted Verification**, **05 Trade Authorization**, and **06 Negative / Security Tests**. The collection variables hold actual base64 from `lineage/tests/fixtures/phase2/n3-v3.bin` and exact fixture-backed mutations. The generator pins the fixture SHA-256 before writing the assets. Postman scripts assert HTTP codes, response fields, verdicts, relevant obligations, permission, verifier identity, and absence of private witness fields.

## Expected results

- **A7 canonical v3:** `VERIFIED`, `lineagePermission: ALLOWED`, finality `PASSED`.
- **A4 pending:** `PENDING_FINALITY`, `lineagePermission: DENIED`, finality `FAILED`.
- **CKN checkpoint:** `CHECKPOINT_TRUST_REQUIRED`, `lineagePermission: DENIED`, finality `FAILED`.
- **Tampered or invalid proof/evidence:** no `VERIFIED` result and lineage permission denied. Transport errors return an `error` object and no verification result.
- **Trade authorization:** it re-verifies the evidence and applies the lineage permission gate. An allowed lineage result is not a completed trade; eligibility remains `NOT_EVALUATED` and settlement `NOT_EXECUTED`.

The A4 and CKN results are available through the demo scenarios while this process is configured for A7. Direct `/v1/trade/authorize` calls use the configured view. To test those statuses directly, stop this process, start it with `ZWA_CHAIN_VIEW=A4` (N3 evidence) or `ZWA_CHAIN_VIEW=CKN` (CKN evidence), and restore A7 afterward. The existing `apps/api/tests/http_integration.py` does that with temporary real processes.

## Reset or stop

The `reorg` demo uses a separate verifier that observes A7 and then switches to pinned B8 during startup. It cannot change the configured production A7 verifier. To reset that in-memory observation state, stop the API with **Ctrl+C** and restart it with the command above. The service reconstructs pinned state on restart; it does not retain a durable chain history. To stop a detached service, use its printed process ID with `kill <PID>`.

## Reproduce validation assets

From `lineage/`, run `python3 postman/generate.py` to rebuild the collection and environment from the pinned fixture, then `python3 postman/validate_live.py` while the A7 demo API is running. The latter executes equivalent live HTTP calls using all collection URLs and bodies, records status, elapsed time, response body, and pass/fail in `postman/live-validation.json`. It does **not** execute Postman's JavaScript test scripts or claim to be the Postman Collection Runner. The generated collection was validated against Postman's published v2.1 JSON Schema; Newman and the Postman CLI were unavailable locally. The owner should run the collection in Postman before pushing.
