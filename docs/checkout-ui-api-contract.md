# Phase 3.5G local checkout UI API contract

This is a **read-only, loopback-only, disposable regtest** API. The UI can show a recorded example and monitor one operator-run local purchase. It cannot initiate a purchase or request a signature. The approved user path is **Choose → Review → Approve → Receive / status**; the Approve step still runs in each independent local wallet CLI, with a human reviewing its own effects. Do not display an actionable Buy/Approve control until that genuine wallet approval path is integrated with the UI.

## Start locally

From `ZWA-Protocol/`:

```sh
python3 -m checkout.api --demo --port 18735 --rpc-port 18243 \
  --session-result /private/tmp/zwa35g/exchange/session-result.json
```

`--demo` is mandatory. The service binds `127.0.0.1` only. Omit `--session-result` to inspect the recorded sample without a live session. The file is emitted by the pinned tx-tool `purchase-coordinator prepare-sale --session-result ...`, then updated by `execute-live` after the independently signed purchase is observed in a block. The complete disposable demonstration is `scripts/phase35g-live-checkout.sh` in the isolated tx-tool branch; set `PRODUCT_DIR` to this product checkout and `PARAMS_DATA_HOME` to the existing local proving-parameter home. It creates a fresh regtest and never reuses the Phase 3.5F evidence. Never run it with production keys or mainnet RPC.

The browser must access the API through the same origin, or a local development proxy that forwards `Host: 127.0.0.1:18735` and does not forward a foreign `Origin`. Direct cross-origin browser access is intentionally rejected. No CORS permission is sent.

Phase 3.5H adds a separate **local terminal** demonstration at `scripts/phase35h-human-checkout.sh` in the isolated tx-tool worktree. The buyer and seller each review and type their own approval inside their sandboxed wallet process. The GET routes and response schema below are unchanged; `signingMode` describes test-wallet custody and does not attest to the human event. The UI must not show an actionable Buy button. A future wallet-owned UI needs an authenticated approval path within the wallet, independent review of the wallet's actual transaction effects, and a safe way to report the result without exposing keys or signed groups through this HTTP service.

## Routes

Only `GET` is supported. Every response is JSON with `Cache-Control: no-store`; unknown routes return `404`. There is no HTTP signing, session creation, purchase, transaction submission or shell-command route.

| Route | Response |
| --- | --- |
| `/v1/checkout/info` | `{version: 1, network: "regtest", demoOnly: true, signingAvailableOverHttp: false, activeSessionLimit: 1, availability, trustSource}` |
| `/v1/checkout/sample-quote` | `{source: "RECORDED_REGTEST_EVIDENCE", actionable: false, quote, status: "RECORDED_REGTEST_EVIDENCE"}` |
| `/v1/checkout/sessions/recorded-phase35f` | Historical quote and receipt, explicitly marked recorded and not actionable. |
| `/v1/checkout/sessions/recorded-phase35f/receipt` | Historical receipt with `finality: "UNKNOWN"`. |
| `/v1/checkout/sessions/{sessionId}` | The one current local CLI handoff, if configured; live chain status is queried on every request. |
| `/v1/checkout/sessions/{sessionId}/receipt` | Live receipt only after CLI execution and chain observation. Before then, `404 RECEIPT_NOT_AVAILABLE`. |

`sessionId` for a live session is the 64-character lowercase `quoteDigest`. The recorded route has a separate literal ID so historical evidence cannot masquerade as a current checkout. A live session file is at most 16 KiB and must match the exact versioned schema, digest, quote ID, outcome and receipt. It contains public quote and receipt data only. The server never reads the wallet seeds, note databases, signed-group envelopes or assembled transaction bytes.

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
