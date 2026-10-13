# Phase 3.5H.1 local checkout UI API contract

This is a **read-only, loopback-only, disposable regtest** API. The UI can show a recorded example and monitor one operator-run local purchase. It cannot initiate a purchase or request a signature. The read-only UI path is **Select → Review → Status → Receipt**. The separate owner-operated terminal harness has completed one manual purchase, but its approval step remains inside each independent wallet CLI. Do not display an actionable Buy/Approve control until a secure wallet approval path is integrated with the UI.

## Start locally

From `ZWA-Protocol/`:

```sh
python3 -m checkout.api --demo --port 18745 --rpc-port 18255
```

`--demo` is mandatory. The service binds `127.0.0.1` only. This exact command serves the recorded Phase 3.5F sample and recorded Phase 3.5H human-run evidence, with no live session. The old disposable node has stopped, so `availability.state` is `NODE_UNAVAILABLE`. To monitor a **new active** harness run, add `--session-result /private/tmp/<fresh-run>/exchange/session-result.json` and use its actual loopback RPC port; do not point a fresh node at an old session file and imply the historical transaction exists on it. The file is emitted by the pinned tx-tool `purchase-coordinator prepare-sale --session-result ...`, then updated by `execute-live` after the independently signed purchase is observed in a block. The complete disposable demonstration is `scripts/phase35g-live-checkout.sh` in the isolated tx-tool branch; set `PRODUCT_DIR` to this product checkout and `PARAMS_DATA_HOME` to the existing local proving-parameter home. It creates a fresh regtest and never reuses the Phase 3.5F evidence. Never run it with production keys or mainnet RPC.

The browser must access the API through the same origin, or a local development proxy that forwards `Host: 127.0.0.1:18745` and does not forward a foreign `Origin`. Direct cross-origin browser access is intentionally rejected. No CORS permission is sent.

Phase 3.5H adds a separate **local terminal** demonstration at `scripts/phase35h-human-checkout.sh` in the isolated tx-tool worktree. The buyer and seller each review and type their own approval inside their sandboxed wallet process. The GET response shapes below remain read-only; `signingMode` describes test-wallet custody and does not attest to the human event. The UI must not show an actionable Buy button. A future wallet-owned UI needs an authenticated approval path within the wallet, independent review of the wallet's actual transaction effects, and a safe way to report the result without exposing keys or signed groups through this HTTP service.

## Routes

Only `GET` is supported. Every response is JSON with `Cache-Control: no-store`; unknown routes return `404`. There is no HTTP signing, session creation, purchase, transaction submission or shell-command route.

| Route | Response |
| --- | --- |
| `/v1/checkout/info` | `{version: 1, network: "regtest", demoOnly: true, signingAvailableOverHttp: false, activeSessionLimit: 1, availability, trustSource}` |
| `/v1/checkout/sample-quote` | `{source: "RECORDED_REGTEST_EVIDENCE", actionable: false, quote, status: "RECORDED_REGTEST_EVIDENCE"}` |
| `/v1/checkout/sessions/recorded-phase35f` | Historical quote and receipt, explicitly marked recorded and not actionable. |
| `/v1/checkout/sessions/recorded-phase35f/receipt` | Historical Phase 3.5F receipt with `finality: "UNKNOWN"`. |
| `/v1/checkout/sessions/recorded-phase35h` | Owner-run human purchase, explicitly recorded, not actionable. |
| `/v1/checkout/sessions/recorded-phase35h/receipt` | Matching owner-run receipt with `finality: "UNKNOWN"` after the chain stopped. |
| `/v1/checkout/sessions/{sessionId}` | The one current local CLI handoff, if configured; live chain status is queried on every request. |
| `/v1/checkout/sessions/{sessionId}/receipt` | Live receipt only after CLI execution and chain observation. Before then, `404 RECEIPT_NOT_AVAILABLE`. |

`sessionId` for a live session is the 64-character lowercase `quoteDigest`. Recorded routes use literal IDs (`recorded-phase35f` and `recorded-phase35h`) so historical evidence cannot masquerade as a current checkout. A recorded receipt has the same literal `sessionId` as its route; `quoteDigest` remains the underlying quote digest. A live session file is at most 16 KiB and must match the exact versioned schema, digest, quote ID, outcome and receipt. It contains public quote and receipt data only. The server never reads the wallet seeds, note databases, signed-group envelopes or assembled transaction bytes.

### QuoteV1

All amounts are **integer base units**. `quantity` is ZSA units; ZEC amounts are zatoshis. The public object has exactly these fields:

```json
{
  "version": 1,
  "quoteId": "cc0883e314fef49e53086f4eeb44fb68b9a95ee071f6e1a3ec9109d9ad532699",
  "network": "regtest",
  "assetId": "a2f7897b34d296f005e728bb660a4f3c19a0dc2e7611d17cddcef5853d000c86",
  "quantity": 7,
  "paymentAsset": "ZEC",
  "paymentAmount": 10000,
  "buyerReceivingCommitment": "04190f08dc94ad65e6c5cad515fc82c4845778cfb068f6dace8d2bc28d02d8e5",
  "sellerProceedsCommitment": "ffd0d082f16e9e3ffd5ba0643d58f7d1cc8f75264f79de5912e05c85b8bb410d",
  "feePayer": "DISPOSABLE_REGTEST_SPONSOR",
  "networkFee": 5000,
  "matcherFee": 0,
  "maximumTotalDebit": 10000,
  "transactionExpiry": 143,
  "consensusBranchId": 2290649224,
  "targetHeight": 103,
  "groupExpiry": 113,
  "sponsorOutpointTxid": "52d16b3f37b8e0a2cc1cf889b5ac7f8be6de18aef724fd601afbef1d9843cf6e",
  "sponsorOutpointIndex": 0,
  "sponsorInput": 625000000,
  "sponsorChange": 624995000,
  "quoteDigest": "73403f0c26335be79bdec4213b5119269e02a4e83f4a1f006fd93fb7b9da9271"
}
```

This example is reconstructed from the **recorded Phase 3.5F purchase**. Its Phase 3.5G `quoteDigest` was derived afterward; it is not the old Phase 3.5F approval hash. The buyer spent **10,000 zatoshis**, received **7 units**, and paid **no network fee**. The separate sponsor spent a 625,000,000-zatoshi coinbase output, received 624,995,000 zatoshis of change and funded the **5,000-zatoshi network fee**. Seller proceeds are 10,000 zatoshis. Never show 15,000 zatoshis as the buyer debit.

The signer and coordinator use the same canonical digest for new sessions: BLAKE2b-256 with 16-byte personalization `ZWA35G_QUOTE_V1\0` over fixed-width little-endian fields in the order shown in `checkout/quote.py` and the pinned tx-tool `QuoteV1`. Network, native ZEC and sponsor are one-byte enum values `01 00 01`. All IDs and receiver commitments are exactly 32 raw bytes. Receiver commitments are SHA-256 of a role-specific domain string plus the 43-byte raw Orchard receiver. Hex is lowercase only. The digest is an **off-chain approval token**, not a consensus signature over the whole transaction. The pinned CLI also independently validates the actual Orchard address encoding and asset base before using it.

The quote includes the additional pinned machinery: SWAP branch ID, next-block target, signed-but-unenforced group expiry, one exact sponsor outpoint and sponsor change. Fee arithmetic is checked, and `maximumTotalDebit = paymentAmount + matcherFee = 10000` here. Unknown fields, noncanonical IDs, unsupported networks/assets, overflow, wrong prices, recipients, fees and sponsor shapes fail closed.

### Live session and receipt

The live session response has `sessionId`, `status`, `availability`, `quote`, and when observed, `receipt`. `availability` is `{state: "AVAILABLE", tipHeight: integer}`, `{state: "NODE_UNAVAILABLE", tipHeight: null}` or `{state: "WRONG_NETWORK", tipHeight: null}`. This pinned Zebra labels its regtest RPC chain as `"test"`, so the API also requires the exact pinned regtest genesis hash, SWAP next-block branch `88888888` and SWAP activation at height 1. A node outage or wrong network returns HTTP `503` and `status: null`; it never reuses a stale accepted status. A submitted transaction is reported only after a matching block hash and txid are observed on the active chain. The API rechecks both on every read.

The receipt contains:

```json
{
  "sessionId": "<quoteDigest>",
  "quoteDigest": "<quoteDigest>",
  "purchaseSettlement": "ACCEPTED",
  "rootedLineage": "UNSUPPORTED",
  "investorEligibility": "NOT_EVALUATED",
  "finality": "PENDING_CONFIRMATIONS_1_OF_3",
  "trustSource": "EXPERIMENTAL_REGTEST",
  "signingMode": "INDEPENDENT_LOCAL_TEST_WALLETS",
  "source": "LIVE_LOCAL_CLI",
  "txid": "<64 lowercase hex>",
  "height": 103,
  "blockHash": "<64 lowercase hex>",
  "txBytes": 28060,
  "confirmations": 1
}
```

At depth 3, session `status` is `FINALIZED` under this local **K=3 depth policy**, while receipt `finality` is `DEPTH_3_REACHED_NOT_CONSENSUS_FINALITY`. A chain reorganization can still invalidate it. One confirmation yields `PENDING_FINALITY`, not `FINALIZED`. Historical receipt `source` is `RECORDED_REGTEST_EVIDENCE` and `finality` is `UNKNOWN` after its ephemeral chain has stopped.

Supported live states are `QUOTE_READY`, `EXPIRED_LOCALLY`, `FAILED`, `PENDING_FINALITY`, `FINALIZED`, and `INVALIDATED_BY_REORG`. `SUBMITTED`, `ACCEPTED`, `AWAITING_WALLET_APPROVAL`, `PREPARING` and `REJECTED` are reserved but not emitted without distinct observed events. A pre-submission CLI failure emits `FAILED`; an uncertain submission leaves `status: null` with `EXECUTION_UNCERTAIN`. `EXPIRED_LOCALLY` is only a local workflow cutoff. It does not revoke a signed group already disclosed to a malicious coordinator.

Errors use `{"error":{"code":"..."}}`: `NOT_FOUND` (404), `RECEIPT_NOT_AVAILABLE` (404), `INVALID_PATH`/`BODY_NOT_ALLOWED` (400), `BROWSER_REQUEST_REJECTED` (403), `READ_ONLY_API` (405), `NODE_UNAVAILABLE`/`WRONG_NETWORK`/`INVALID_NODE_RESPONSE`/`INCONSISTENT_CHAIN_VIEW`/`INVALID_SESSION_RESULT`/`EXECUTION_UNCERTAIN` (503). Treat any 503 as unavailable or unresolved, never as a completed purchase.

For UI layout, show **asset and quantity**, **buyer price and separate sponsor fee**, **wallet approval state**, then **receipt/status**. Put txid, block hash, commitments, expiry and experimental trust labels in a details panel. `rootedLineage: UNSUPPORTED` and `investorEligibility: NOT_EVALUATED` must not be rendered as verified ownership or legal RWA authentication.

## Phase 3.5H.1: concrete read-only UI handoff

Start from `ZWA-Protocol/` with `python3 -m checkout.api --demo --port 18745 --rpc-port 18255`. The exact base URL is `http://127.0.0.1:18745`. The process must remain running while the UI reads it. The server binds loopback only. The recorded human session is always available from the local fixture, but its historical chain is not live. For a new operator-run session, start the API with that fresh coordinator's `--session-result` path and matching active regtest `--rpc-port`; poll the 64-character lowercase `quoteDigest` returned as `sessionId` by the coordinator. The service serves at most one such live session. Poll only while the API is reachable and the session is active; there is no push channel or public transaction creation route.

These are full JSON response bodies from the current API with the owner-run node stopped. They show the four GET response shapes using actual values. The sample quote is **Phase 3.5F recorded evidence**; the session and receipt are **Phase 3.5H owner-run recorded evidence**. Neither is an actionable order.

### `GET /v1/checkout/info` — HTTP 200

```json
{
  "version": 1,
  "network": "regtest",
  "demoOnly": true,
  "signingAvailableOverHttp": false,
  "activeSessionLimit": 1,
  "availability": {
    "state": "NODE_UNAVAILABLE",
    "tipHeight": null
  },
  "trustSource": "EXPERIMENTAL_REGTEST"
}
```

### `GET /v1/checkout/sample-quote` — HTTP 200

```json
{
  "source": "RECORDED_REGTEST_EVIDENCE",
  "actionable": false,
  "quote": {
    "version": 1,
    "quoteId": "cc0883e314fef49e53086f4eeb44fb68b9a95ee071f6e1a3ec9109d9ad532699",
    "network": "regtest",
    "assetId": "a2f7897b34d296f005e728bb660a4f3c19a0dc2e7611d17cddcef5853d000c86",
    "quantity": 7,
    "paymentAsset": "ZEC",
    "paymentAmount": 10000,
    "buyerReceivingCommitment": "04190f08dc94ad65e6c5cad515fc82c4845778cfb068f6dace8d2bc28d02d8e5",
    "sellerProceedsCommitment": "ffd0d082f16e9e3ffd5ba0643d58f7d1cc8f75264f79de5912e05c85b8bb410d",
    "feePayer": "DISPOSABLE_REGTEST_SPONSOR",
    "networkFee": 5000,
    "matcherFee": 0,
    "maximumTotalDebit": 10000,
    "transactionExpiry": 143,
    "consensusBranchId": 2290649224,
    "targetHeight": 103,
    "groupExpiry": 113,
    "sponsorOutpointTxid": "52d16b3f37b8e0a2cc1cf889b5ac7f8be6de18aef724fd601afbef1d9843cf6e",
    "sponsorOutpointIndex": 0,
    "sponsorInput": 625000000,
    "sponsorChange": 624995000,
    "quoteDigest": "73403f0c26335be79bdec4213b5119269e02a4e83f4a1f006fd93fb7b9da9271"
  },
  "status": "RECORDED_REGTEST_EVIDENCE"
}
```

### `GET /v1/checkout/sessions/recorded-phase35h` — HTTP 200

```json
{
  "sessionId": "recorded-phase35h",
  "status": "RECORDED_REGTEST_EVIDENCE",
  "availability": {
    "state": "NODE_UNAVAILABLE",
    "tipHeight": null
  },
  "quote": {
    "assetId": "18f9a9c9f6cfcff0416560beddfd6b31c10b37d83cfc32ee9d9b1b01b0bcd834",
    "buyerReceivingCommitment": "146121352c001aa36b79bef9a8430259ec6a19e4799d3bf5430e317f85c18fdb",
    "consensusBranchId": 2290649224,
    "feePayer": "DISPOSABLE_REGTEST_SPONSOR",
    "groupExpiry": 113,
    "matcherFee": 0,
    "maximumTotalDebit": 10000,
    "network": "regtest",
    "networkFee": 5000,
    "paymentAmount": 10000,
    "paymentAsset": "ZEC",
    "quantity": 7,
    "quoteDigest": "bcfeece4d3e52834990a51780e772fd46d8b85ac90255e71b6f854afa9707f0d",
    "quoteId": "53687f91a5f1f8aa9be7f960c8910a0516bf90296ee08e3862c36a0a38a85e86",
    "sellerProceedsCommitment": "c69e3f0dce225da6a03a1cb51dbcd9fefa48a8a65d466d9e0c95a5b881ada218",
    "sponsorChange": 624995000,
    "sponsorInput": 625000000,
    "sponsorOutpointIndex": 0,
    "sponsorOutpointTxid": "52d16b3f37b8e0a2cc1cf889b5ac7f8be6de18aef724fd601afbef1d9843cf6e",
    "targetHeight": 103,
    "transactionExpiry": 143,
    "version": 1
  },
  "receipt": {
    "sessionId": "recorded-phase35h",
    "quoteDigest": "bcfeece4d3e52834990a51780e772fd46d8b85ac90255e71b6f854afa9707f0d",
    "purchaseSettlement": "ACCEPTED",
    "rootedLineage": "UNSUPPORTED",
    "investorEligibility": "NOT_EVALUATED",
    "finality": "UNKNOWN",
    "trustSource": "EXPERIMENTAL_REGTEST",
    "signingMode": "INDEPENDENT_LOCAL_TEST_WALLETS",
    "source": "RECORDED_REGTEST_EVIDENCE",
    "txid": "aaeb15b837d5b44fc6aaf6d88b36ac9b802cd53e6cd2cf292ef8450db6826dfd",
    "height": 103,
    "blockHash": "7ece7eadb0fda860e9d2f49add753174da9a04d4e64a4a1c3ced90e15f0cd13a",
    "txBytes": 28060,
    "confirmations": null
  },
  "actionable": false
}
```

### `GET /v1/checkout/sessions/recorded-phase35h/receipt` — HTTP 200

```json
{
  "sessionId": "recorded-phase35h",
  "quoteDigest": "bcfeece4d3e52834990a51780e772fd46d8b85ac90255e71b6f854afa9707f0d",
  "purchaseSettlement": "ACCEPTED",
  "rootedLineage": "UNSUPPORTED",
  "investorEligibility": "NOT_EVALUATED",
  "finality": "UNKNOWN",
  "trustSource": "EXPERIMENTAL_REGTEST",
  "signingMode": "INDEPENDENT_LOCAL_TEST_WALLETS",
  "source": "RECORDED_REGTEST_EVIDENCE",
  "txid": "aaeb15b837d5b44fc6aaf6d88b36ac9b802cd53e6cd2cf292ef8450db6826dfd",
  "height": 103,
  "blockHash": "7ece7eadb0fda860e9d2f49add753174da9a04d4e64a4a1c3ced90e15f0cd13a",
  "txBytes": 28060,
  "confirmations": null
}
```

The Phase 3.5H quote has `quantity: 7`, `paymentAmount: 10000` zatoshis, `networkFee: 5000` zatoshis paid by `DISPOSABLE_REGTEST_SPONSOR`, `matcherFee: 0`, and `maximumTotalDebit: 10000` zatoshis. Render the buyer's debit as **10,000**, with the sponsor fee separately labeled. `assetId` is the 32-byte custom ZSA base ID; `quoteDigest` is the off-chain approval token; `targetHeight`, `transactionExpiry`, and `groupExpiry` are block heights. `groupExpiry` is not enforced by pinned Zebra. The receipt's `txid` and `blockHash` are 64-character lowercase transaction and block identifiers, `height` is the inclusion height, `txBytes` is serialized transaction size, and `confirmations` is `null` for recorded evidence. The API does not put buyer and seller balance deltas in the receipt; the independently observed effects are documented in `checkout-phase35h-human-approval.md` and should not be invented as receipt fields.

For a genuinely live session, `QUOTE_READY` means a quote exists but no accepted transaction is observed. After a matching current-chain block and txid are checked, the API emits `PENDING_FINALITY` at depths one and two, then `FINALIZED` at depth three under the local K=3 policy. Receipt `purchaseSettlement: ACCEPTED` means block inclusion was observed; `finality: DEPTH_3_REACHED_NOT_CONSENSUS_FINALITY` expressly disclaims irreversible consensus finality. A reorg can produce `INVALIDATED_BY_REORG`. The receipt must retain `rootedLineage: UNSUPPORTED` and `investorEligibility: NOT_EVALUATED`; neither implies a legally issued or compliant RWA. `trustSource: EXPERIMENTAL_REGTEST` and `signingMode: INDEPENDENT_LOCAL_TEST_WALLETS` describe the test environment, not a browser wallet or proof of who approved.

UI handling: **Select** shows the asset; **Review** shows seven units, 10,000-zatoshi authorized buyer payment, zero matcher fee and the separate sponsor-paid 5,000-zatoshi network fee; **Status** displays the actual session state and chain availability; **Receipt** displays identifiers and qualified finality only when returned. A 404 `NOT_FOUND` means the requested session is absent or malformed. A 404 `RECEIPT_NOT_AVAILABLE` means a valid live session has no observed receipt yet. A 503 with `status: null` means live execution is unverified; display its `error.code` and never infer success from a stale local file. If the API itself is down, treat the transport error as `UNAVAILABLE`, not as a failed or accepted purchase. Recorded sessions remain explicitly `RECORDED_REGTEST_EVIDENCE` with `finality: UNKNOWN` even if a new disposable chain is started.

Direct cross-origin browser calls fail strict Host, Origin and Fetch Metadata checks, and the API sends no permissive CORS headers. Use a same-origin local development proxy with an explicit upstream Host rewrite only for read-only GETs. Do not proxy a wallet command, Unix signer socket, signed group, arbitrary shell execution, or secret material. There is no secure browser-to-wallet approval protocol or actionable Buy button. Buyer and seller Action Group signatures do not cover all final transaction effects, and a local timeout cannot revoke a disclosed group.

## Phase 4B active-session observation extension

`GET /v1/checkout/active` discovers only the one operator-configured session. It does not accept a caller-selected session ID. With no `--session-result`, it returns 404 `{ "error": { "code": "NO_ACTIVE_SESSION" } }`. The operator launcher starts the API with `--session-result`, `--event-result`, and `--wallet-scans` pointing to private per-run public observation files. Those files are created by the operator's local CLI; no HTTP request can write them.

The live response retains the session fields above. Optional `operatorStages` is an ordered list from `SESSION_PREPARED`, `AWAITING_BUYER_APPROVAL`, `BUYER_AUTHORIZED`, `AWAITING_SELLER_APPROVAL`, `SELLER_AUTHORIZED`, `TRANSACTION_SUBMITTED`, `TRANSACTION_INCLUDED`, `CONFIRMATION_DEPTH_OBSERVED`, `RECEIPT_AVAILABLE`; a terminal `*_APPROVAL_NOT_GRANTED`, `APPROVAL_TIMEOUT`, `SIGNER_UNAVAILABLE`, `EXECUTION_FAILED`, or `EXECUTION_UNCERTAIN` may follow the observed prefix. `operatorStage` is its last element. `approvalEvidence` is always `LOCAL_OPERATOR_EVENT_ONLY`: it does not attest to who typed, and is not a signature. The launcher records `TRANSACTION_SUBMITTED` and `TRANSACTION_INCLUDED` after its coordinator has returned a real accepted receipt; it never predicts them from a quote. The API rejects mismatched, unordered, oversized or symlinked event files with HTTP 503 `INVALID_SESSION_OBSERVATION`.

Optional `walletScans` appears only with a receipt that the API has rechecked against the active block and when the buyer and seller's independent scanner output reconciles with the exact quote. It contains `version: 1`, `sessionId`, `assetId`, and `buyerBefore`, `buyerAfter`, `sellerBefore`, `sellerAfter`, each with unsigned `height`, `nativeZat`, and `assetUnits`. It is also attached to the live receipt response. A mismatch in asset, session, receipt height or balance effects returns HTTP 503 rather than a green effect claim. These are local wallet scan observations, not a lineage proof. The recorded receipt routes remain unchanged.

The Next.js site exposes only the fixed same-origin `GET /api/checkout/active` mapping to this route. It polls without caching and clears the live view on API loss or non-200 status. Browser control, signing and transaction submission remain disabled. See `docs/phase4b-operator-runbook.md` for the launch sequence and `docs/phase4b-implementation-report.md` for the cryptographic boundary.
