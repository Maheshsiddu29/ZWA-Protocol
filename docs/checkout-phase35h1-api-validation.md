# Phase 3.5H.1 checkout API validation

## Result and evidence boundary

The owner-operated Phase 3.5H purchase is recorded as `HUMAN_APPROVED_PURCHASE_ACCEPTED` in [checkout-phase35h-human-approval.md](checkout-phase35h-human-approval.md). The evidence in `/private/tmp/zwa35h/` includes the public session result, API snapshots, independent block and transaction RPC responses, role logs, wallet-scan lines and scoped leak-audit logs. Owner testimony establishes who typed the approvals; the retained logs establish that each role authorized the same digest and the coordinator executed. The disposable node was stopped. The historical transaction is **not** currently live-queryable; the API exposes it through `recorded-phase35h` and labels its finality `UNKNOWN` on that route.

The checkout API remains a read-only loopback bridge. The added fixture contains only public quote and receipt data. It does not contain wallet homes, note openings, secrets, signed groups or assembled transaction bytes.

## Port 18745 diagnosis and fix

`phase35h-human-checkout.sh` starts the Python service itself with `PYTHONPATH="$PRODUCT_DIR" python3 -m checkout.api --demo --port "$API_PORT" --rpc-port "$PORT" --session-result ... &`; the default API port is **18745** and the service binds `127.0.0.1` only. `phase35g-live-checkout.sh` follows the same pattern on its own configured port. The service starts asynchronously and the harness exit trap terminates it; it is not a standing daemon. The former loop probed immediately with `curl -fsS`; every connection-refused retry printed an error, and after 40 attempts it fell through to the next GET without a dedicated readiness failure. The owner run's API log contains the successful loopback bind line; saved `api-info.json` and later API snapshots prove the service subsequently answered. No retained evidence shows a process crash or missing API process in that successful run. The refusal was a startup race, but the old fall-through could have concealed a real startup failure.

Both harnesses now wait for an actual `GET /v1/checkout/info` response with version 1, `network: regtest`, `signingAvailableOverHttp: false`, and `availability.state: AVAILABLE`. The loop is bounded to 100 probes with 1-second per-probe timeout and 0.1-second retry spacing. Transient curl stderr goes to a local diagnostic file. A premature child exit reports its exit status and API log; timeout reports the last curl error, info response and API log and exits nonzero before wallet approval. A delayed-bind test and early-exit test passed. The API itself does not need to be made public or gain a new health route. Its `info` endpoint can return HTTP 200 with `NODE_UNAVAILABLE`; the harness correctly treats that as not ready because its node must be live.

## Running HTTP endpoint matrix

On a fresh service at `127.0.0.1:18745` with `--rpc-port 18255` and the old public session file, 13 real HTTP cases passed:

- `GET /v1/checkout/info` → 200; regtest, demo only, signing unavailable over HTTP, experimental trust source, `NODE_UNAVAILABLE` for the stopped node.
- `GET /v1/checkout/sample-quote` → 200; historical Phase 3.5F quote, seven units, 10,000-zatoshi buyer debit, sponsor-paid 5,000-zatoshi network fee, zero matcher fee, `actionable: false`.
- `GET /v1/checkout/sessions/recorded-phase35f` and `/receipt` → 200; both marked recorded, with receipt `sessionId` matching the literal route. The prior mismatch was fixed.
- `GET /v1/checkout/sessions/recorded-phase35h` and `/receipt` → 200; owner-run txid `aaeb15b837d5b44fc6aaf6d88b36ac9b802cd53e6cd2cf292ef8450db6826dfd`, height 103, block `7ece7eadb0fda860e9d2f49add753174da9a04d4e64a4a1c3ced90e15f0cd13a`, `finality: UNKNOWN`, unsupported lineage and unevaluated eligibility. Receipt `sessionId` matches `recorded-phase35h`; the quote digest is a separate field.
- `GET /v1/checkout/sessions/bcfeece4d3e52834990a51780e772fd46d8b85ac90255e71b6f854afa9707f0d` and `/receipt` → 503, `status: null`, `NODE_UNAVAILABLE`; the old public session file is not promoted to live success without its original node.
- Missing and malformed session IDs → 404 `NOT_FOUND`.
- `POST` → 405 `READ_ONLY_API`; foreign Origin and Host → 403 `BROWSER_REQUEST_REJECTED`. Responses had `Cache-Control: no-store` and no permissive CORS header.

The original owner-run saved API snapshots show `QUOTE_READY` at tip 102, `PENDING_FINALITY` and receipt depth one at 103, and `FINALIZED` with depth three at tip 105. Independent saved RPC evidence contains the txid in the reported block and a 28,060-byte raw transaction matching the receipt. The API source rechecks matching block hash, txid and confirmation count for live reads. A separate real HTTP run against a **simulated local RPC** exercised `QUOTE_READY` (200), `RECEIPT_NOT_AVAILABLE` (404), `PENDING_FINALITY` at depth one (200), live receipt depth one (200), `FINALIZED` at depth three (200), then `NODE_UNAVAILABLE` (503) after the mock node stopped while recorded evidence stayed available (200). This is a transport/status simulation, not another purchase. Checkout tests also cover inconsistent chain views and reorg; no new transaction was submitted for this phase. The receipt does not contain observed buyer and seller balance deltas; these come from the independent wallet scans documented in the human-approval report. It must not be rendered as an independently decoded transfer-effects proof.

Full exact JSON response examples and field units are in [checkout-ui-api-contract.md](checkout-ui-api-contract.md). The local UI starts the API from `ZWA-Protocol/` with `python3 -m checkout.api --demo --port 18745 --rpc-port 18255`, then uses `http://127.0.0.1:18745` through a same-origin local proxy for browser access. Use the recorded human route for this finished run. A new live session requires a fresh operator-run coordinator, its own session-result path and its active regtest RPC port. There is no browser signing or public Buy action.

## Postman and security

A separate Collection v2.1 asset is at `checkout/postman/ZWA-Checkout-ReadOnly.postman_collection.json`; its README identifies requests that require a fresh quote-ready session and an intentionally unavailable backend. All nine requests are GET. JSON parsing and all eight assertion scripts' JavaScript syntax passed. Postman/Newman is unavailable here, so no Postman Runner pass is claimed. The equivalent real HTTP smoke checks above ran directly against the service.

The API retains loopback-only binding, strict Host/Origin/Fetch Metadata checks, no permissive CORS, no signing route, bounded paths/session files/RPC reads, and fail-closed live status. The CLI signer remains behind its local Unix socket and the buyer/seller private homes. No shell-command endpoint was added. Pinned Zebra does not enforce Action Group expiry; timeout cannot revoke a disclosed group. Buyer and seller group signatures do not cover every final transaction effect. Neither the API nor UI may claim production custody, rooted-lineage settlement, investor eligibility, legally issued RWA status, or irreversible finality.

## Validation record

- Checkout Python tests: 8/8 passed with local socket permission. The new recorded-session checks cover literal `sessionId`/receipt matching and the exact owner-run txid.
- Real checkout HTTP smoke: 13/13 passed with local loopback permission. API bound to `127.0.0.1:18745`; it returned `NODE_UNAVAILABLE` for the stopped node and did not report stale success. A separate seven-step HTTP progression against simulated RPC passed quote-ready, missing receipt, depths one and three, node loss and recorded evidence availability.
- Harness shell syntax: `bash -n` passed for Phase 3.5G and Phase 3.5H scripts. Delayed-bind and early-process-exit readiness checks passed without a purchase.
- Tx-tool: `cargo fmt --all -- --check` and strict offline locked Clippy passed. The first restricted `cargo test --offline --locked` run failed three IPC tests because the sandbox denied Unix-socket creation; with local socket permission all 22 unit tests, one acceptance test and zero doc tests passed. No test expectations changed.
- Product `lineage/scripts/check.sh`: passed fixture/source pins, formatting, strict Clippy, workspace tests and unchanged lockfile. Existing Phase 3 HTTP integration: 46/46 assertions passed; its 12 timing-only evidence rewrites were restored to committed bytes.
- Frozen research `lineage/scripts/check-research.sh`: exit 0; 5 differential, 1 fixture-source, 1 full Layer-B core and 11 Phase 2 tests passed (one fixture-regeneration test intentionally ignored). The 236-case matrix check passed: 236 unique authoritative cases, 201 agreements, zero divergences, 34 not portable.
- Postman collection: JSON parsed, all request methods verified GET, eight embedded JavaScript test scripts parsed; Runner unavailable.

The older zsh validation wrapper's read-only `status` variable was a one-off command issue, not a repository script. This phase used `check_exit` for surrounding commands, so a command exits 0 only if the underlying check passed. Shell harnesses use Bash-local `status` variables, which are valid there.

No new dependency, lockfile, circuit, VK, parameter, Zebra consensus, frozen research or pinned cryptographic rule was changed. No push, merge, PR, deployment, publication or mainnet transaction occurred.

## Verdict and owner handoff

**CHECKOUT_READ_ONLY_UI_READY** for local, read-only regtest integration. The API starts on loopback, all four GET endpoint shapes were exercised against a running service, historical and live evidence are separated, the owner-run receipt matches the public transaction evidence, the readiness race is handled, and the regression gates above passed. This verdict does not authorize an actionable Buy button. The frontend still needs a secure browser-to-wallet approval protocol before it can initiate a purchase; it must use a same-origin local proxy for this strict-Host API. The stopped owner-run chain is unavailable for fresh finality queries. Postman Runner execution remains an owner follow-up if desired; this validation used equivalent real HTTP requests.

Phase 3.5H.1 changed only the checkout API, its public recorded fixture, API tests, read-only Postman assets, two harness readiness loops, and checkout documentation. The owner review should keep `ZWA-Protocol/.cursor/` untouched. **PRE-EXISTING ZK-ORIGIN** and QED-it attribution is preserved; these checkout additions are **NEW ZWA PROTOCOL WORK**. Files deleted: none. Architecture change: one additional explicitly recorded session route and a matching receipt ID; no signing or transaction path added. Security impact: fail-closed startup and accurate recorded/live labels, with strict browser boundary unchanged. Dependencies changed: none.
