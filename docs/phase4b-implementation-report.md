# Phase 4B implementation and evidence report

> This is the pre-recovery snapshot. The trusted parameter path was recovered and two fresh scripted checkouts passed on 2026-10-12. See [Phase 4B recovery report](phase4b-recovery-report.md) for the current evidence and human handoff. No new human wallet approvals have been given.

## Verdict and source state

**PARTIAL / BLOCKED** as of 2026-10-12. The local product implementation, read-only UI observation, and regression tests are in place. A new accepted transaction and a second repeat run are **not yet evidenced**. The first new scripted test-mode attempt stopped before funding because the existing `~/.local/share/ZcashParams` link points to a missing directory; the pinned tx-tool reported `Zcash parameters not found`. No parameter fetch script was run. The owner has not yet supplied buyer and seller approvals for this Phase 4B run. Do not classify this as `REAL_WALLET_E2E_WITH_SEPARATE_LINEAGE_EVIDENCE` until a new accepted regtest transaction, wallet scans, and human decisions are recorded.

The work branch is `integration/phase4b-real-e2e-demo` (current local HEAD is the report commit; see `git rev-parse HEAD`) from the Phase 4 website head `cc94456573d525268b3f66b582fad3efd9cc2cb5`. That head includes the product checkout/lineage head `5275e4f` and all nine Phase 4 website commits. The original `integration/zwa-checkout-poc` worktree, untracked `.cursor/` and `design-assets/`, and detached UI review worktree remain intact. The isolated tx-tool remains at `3dd2a010fd557ef15a68915e42fec8289dc7d7d2`. Pinned Zebra image: `sha256:540a3429ef14ff3c575258255e4310951606d16c86ec5e1c37e285ea95178185`. No frozen circuit, verification key, parameter, consensus rule, pinned source or remote state was changed. PRE-EXISTING ZK-ORIGIN and QED-it attribution is preserved; the additions here are NEW ZWA PROTOCOL WORK.

## Implemented journey and architecture

The operator launcher is implemented to create a fresh disposable asset and quote, obtain independent terminal wallet decisions, coordinate one transaction, check inclusion, rescan wallets and keep the local node alive for inspection. That full sequence has not yet completed in Phase 4B. The site presents Asset → Verification → Quote → Approval → Settlement → Receipt, polling the read-only active route. It leads with the product flow; the independent verifier is below it. Historical Phase 3.5H evidence is available in a separate disclosure. The UI has no purchase or signing command.

```text
Next.js /demo → fixed same-origin GET proxy → loopback checkout API
                                               ↳ public session / event / scan files
                                               ↳ pinned Zebra RPC for chain identity and receipt
local operator launcher → buyer signer ─┐
                        → seller signer ─┼→ coordinator → Zebra regtest
                        → sponsor ────────┘
separate Next.js verifier proxy → pinned A7 research verifier
```

New endpoint: `GET /v1/checkout/active`. It returns the same live session shape as `/v1/checkout/sessions/{quoteDigest}` for the one configured session, with optional `operatorStages: string[]`, `operatorStage: string`, `approvalEvidence: "LOCAL_OPERATOR_EVENT_ONLY"`, and, after reconciled scans and live chain verification, `walletScans`. No configured session returns 404 `NO_ACTIVE_SESSION`; invalid observation files return 503 `INVALID_SESSION_OBSERVATION`; node loss and wrong chain remain 503 with no successful status. `walletScans` has `version: 1`, `sessionId`, `assetId`, and `buyerBefore`, `buyerAfter`, `sellerBefore`, `sellerAfter`, each containing unsigned `height`, `nativeZat`, `assetUnits`. It is also attached to the live receipt response. The site exposes only `/api/checkout/active` as an additional fixed proxy target. Existing endpoints and historical receipts retain their schemas and meanings.

## Proof-to-transaction binding gate

| Evidence element | Actual supported statement | Link to fresh checkout | Assessment |
| --- | --- | --- | --- |
| Proof statement | Frozen `NormalLineageStepV2` proves non-split, non-reference rho-backbone note relations; Design A checks proof sequence and exact `cmx` joins | The new swap output is not shown to satisfy this relation | Unsupported for purchase output |
| Issued asset ID | Fresh seller issuance yields an actual 32-byte AssetBase; `QuoteV1.assetId` uses those bytes and the signer/coordinator enforce it | Byte equality within the local quote and transaction flow, not an authenticated lineage root | Supported as checkout identity only |
| Chain context | Verifier uses pinned A7/A4/etc. authenticated historical snapshots; checkout uses a separately started pinned experimental Zebra regtest with checked genesis and SWAP branch | No shared authenticated snapshot or chain-ref binding | Unsupported across stacks |
| Checkout asset ID | Exact public quote digest covers asset, quantity, price, recipients, fee payer, fee and sponsor fields | Wallet and coordinator validate the digest and own effects | Supported off-chain policy; digest is not final consensus sighash |
| Spent input note | Each signer scans and chooses its own wallet notes, derives effects, and signs its Action Group | No lineage proof binds those private note witnesses to an authenticated issuance root | Unsupported lineage binding |
| Received output note | Coordinator validates assembled transaction and live API checks txid in matching block; wallets rescan effects | No valid proof connects the output to the frozen normal lineage terminal | Unsupported lineage binding |
| Cryptographic binding | Research proof has its own verified public `cmx` sequence; Phase 0 TradeCommitmentV1 has separate Poseidon public signals | Neither public signal equals an enforced commitment in this Phase 3.5 checkout | Full proof-to-settlement binding unsupported |
| Trust assumptions | A7 fixture policy/source pins vs disposable Zebra and local operator event files | Different roots and lifetimes; operator events do not attest human identity | Separate trust domains |

Path A is blocked by the frozen relation's unsupported split/reference swap shape and lack of fresh same-chain proof generation. Path B supports exact local AssetBase and quote binding, but the fresh issuance is not authenticated by the pinned verifier's root policy. Path C is not integrated: the older Circom provenance/eligibility tests prove their own `TradeCommitmentV1` relation, but this checkout neither generates those proofs for fresh notes nor checks a byte-equal commitment at settlement. No `VERIFIED` result in the pinned verifier authorizes this purchase. A genuinely cryptographically gated path must fail closed; this demonstration is explicitly ungated.


## Receipt and approval evidence currently available

The recorded Phase 3.5H receipt has quote digest `bcfeece4d3e52834990a51780e772fd46d8b85ac90255e71b6f854afa9707f0d`, txid `aaeb15b837d5b44fc6aaf6d88b36ac9b802cd53e6cd2cf292ef8450db6826dfd`, height 103, block `7ece7eadb0fda860e9d2f49add753174da9a04d4e64a4a1c3ced90e15f0cd13a`, and size 28,060 bytes. Its recorded API `finality` is `UNKNOWN`; `rootedLineage` is `UNSUPPORTED`; `investorEligibility` is `NOT_EVALUATED`. Historical wallet scans were buyer 625,000,000 → 624,990,000 ZEC zatoshis and 0 → 7 ZSA units, seller 0 → 10,000 ZEC zatoshis and 100 → 93 ZSA units. These are the old owner-run observations and are **not** Phase 4B output.

The Phase 3.5H owner reported typing the buyer and seller approvals manually; retained local logs showed both wallet roles authorized the digest. The new Phase 4B scripted attempt did not reach approval. The new human launcher contains only `authorize-interactive`, with direct terminal prompts and no scripted-test-mode flag. Until the owner actually runs it and confirms their decisions, Phase 4B has **no human approval evidence**.

## Validation, screenshots and open gates

- Checkout API: **10/10** Python tests passed with loopback permission, including quote mutations, active discovery, event order, asset/session/receipt scan mismatch, malformed session, node unavailable, simulated reorg, historical separation and browser Host/Origin restrictions. `bash -n` and Python compilation passed.
- Website: `npm ci --offline` completed with zero reported vulnerabilities; TypeScript check and production build passed. Real same-origin HTTP smoke returned `/demo` 200, `/api/checkout/info` 200, `/api/checkout/active` 404 `NO_ACTIVE_SESSION`, recorded session and receipt 200, and an unknown path 404. With the API stopped, the browser displayed `BACKEND_UNAVAILABLE` and no live success.
- Browser: the live A7 research scenario returned `VERIFIED`, `PINNED_REGTEST_FIXTURE`, `NOT_EVALUATED` eligibility and `NOT_EXECUTED` settlement. The historical disclosure showed the Phase 3.5H txid with `UNKNOWN` current finality. At 1440, 1024, 390 and 320 pixels, `documentElement.scrollWidth` equaled the viewport. Screenshots: [1440](../website/docs/screenshots/phase4b/demo-1440.jpg), [1024](../website/docs/screenshots/phase4b/demo-1024.jpg), [390](../website/docs/screenshots/phase4b/demo-390.jpg), [320](../website/docs/screenshots/phase4b/demo-320.jpg), [API down](../website/docs/screenshots/phase4b/api-down-1440.jpg). These are no-active-session screenshots, not live purchase evidence.
- Verifier: `lineage/scripts/check.sh` passed source and fixture pins, format, strict Clippy, workspace tests and unchanged lockfile. The real Phase 3 HTTP regression passed **46/46**, writing generated evidence to `/private/tmp` so pinned evidence files stayed unchanged. `lineage/scripts/check-research.sh` passed the frozen differential suite: 5 differential, 1 fixture-source, 1 full Layer-B, and 11 Phase 2 tests passed, with one fixture-regeneration test intentionally ignored. The 236-case matrix had 201 agreements, zero divergences and 34 not portable cases.
- Tx-tool: strict offline Clippy passed. The sandbox-only socket tests initially failed due local IPC denial; rerunning with local socket permission passed **22 unit tests**, one acceptance smoke test, and doc tests. The isolated tx-tool source remained unmodified.
- Operator: the pinned Docker image exists. A new **scripted test-mode** disposable regtest run started a fresh chain and wallet keys and mined 100 blocks, then stopped at shielded funding because the local proving-parameter directory was missing. This is **not** a transaction and is **not** human approval. The disposable container was stopped. The Phase 4B human launcher has not yet run end to end.
- Negative cases covered by local tests include invalid quote digest, wrong asset, quantity, buyer debit, fee, wrong network, malformed and mismatched session/receipt, unordered or duplicate event, unrelated session scan, unreconciled wallet effect, missing signer, duplicate approval, replayed nonce, stale approval window, node loss, inconsistent block confirmations and simulated reorg. The pinned verifier suite rejects invalid proof, wrong root, wrong terminal and wrong chain context. Fresh live buyer rejection, seller rejection, coordinator crash, reorg, and second accepted run remain untested.
- Fresh Phase 4B txid: **none**. Fresh wallet scan balances and a new receipt payload: **none**. Prior historical Phase 3.5H txid `aaeb15b837d5b44fc6aaf6d88b36ac9b802cd53e6cd2cf292ef8450db6826dfd` is retained as recorded evidence only.

Security review: the API remains GET-only, loopback-bound, strict on Host/Origin/Fetch Metadata, has no CORS or wallet command, and validates bounded public session/event/scan files. Browser clients receive no seeds, private witnesses or signed Action Groups. The operator launcher requires a terminal and uses signer `authorize-interactive` with default rejection. Event stages are operator observations, not signed audit records. The pinned Zebra does not consensus-enforce Action Group expiry, and party signatures do not cover every final combined-transaction field. Never reuse a released group or call local K=3 depth irreversible finality.

See [the operator runbook](phase4b-operator-runbook.md) for exact startup, approval, inspection and recovery steps. A two-minute presentation: (1) show the fresh AssetBase and exact quote; (2) point out that the research proof is separate and the checkout lineage is unsupported; (3) have buyer and seller independently approve in their local wallet terminals; (4) watch observed session stages and chain inclusion; (5) inspect txid, block, scan effects, fee and depth-qualified receipt. Until the missing parameters and owner approval are available, present the historical receipt as explicitly recorded evidence and say the Phase 4B live gate is pending.

## Release gate decisions

- **A — Foundations:** Passed for the existing website, verifier, checkout API, tx-tool unit/IPC tests and build. The fresh operator path remains unaccepted.
- **B — Same-chain integration assessment:** Complete. No supported byte-level bridge from the pinned research proof/root to the fresh swap input, output and transaction.
- **C — Real execution:** Blocked before funding by missing trusted proving parameters; no new accepted transaction or Phase 4B human approvals.
- **D — UI observation:** Passed for live-session API contract, no-session, historical disclosure and API-down browser states. A real live purchase screenshot remains open.
- **E — Claim integrity:** The UI labels the A7 `VERIFIED` verdict as an independent research demonstration and the purchase lineage as unsupported.
- **F — Reproducibility:** Blocked; no first accepted run, so no second accepted run.

## Local commits and next evidence

- `1847287` — read-only active observations, strict session/event/scan checks and API contract.
- `cbf448d` — fixed website proxy and branded live checkout page.
- `78be689` — pinned human wallet launcher and operator runbook.
- `6548e40` — product-first page order and explicit independent verifier boundary.
- `7700399` — evidence report, screenshots and fail-closed observation hardening.
- `e5c248c` — early occupied-port and container-name checks for the operator launcher.
- The latest report text is in the final local documentation commit.

The next accepted run must record the actual new txid, block, buyer/seller before-and-after scans, public API receipt, direct owner confirmation that both approvals were typed manually, and a second fresh run following the runbook. The browser's current no-session screenshots cannot substitute for a live session screenshot. No push, merge, PR or deployment has occurred.
