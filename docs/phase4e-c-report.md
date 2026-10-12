# Phase 4E-C review gate: browser-initiated, wallet-approved purchase

**Verdict: READY FOR OWNER HUMAN APPROVAL. The human-approved run has not taken place.**
A purchase initiated in the browser completed end to end on a separate
disposable chain in **SCRIPTED_TEST_MODE**. On the protected chain everything
is prepared and stops at the owner's two wallet approvals
([runbook](phase4e-c-runbook.md)). Nothing in this report is `HUMAN_APPROVED`.
Do not claim `COMPLETE_EXPERIMENTAL_EXCHANGE`.

## 1. Goal and order lifecycle

Pressing Buy now starts a real quote. A purchase advances only on backend
evidence:

`BUYER_REQUESTED → QUOTE_READY → BUYER_CONFIRMED → PROOF_VERIFIED →
TRADE_AUTHORIZED → BUYER_APPROVED → SELLER_APPROVED → SUBMITTED → INCLUDED →
RECONCILED → RECEIPT_AVAILABLE`, with fail-closed exits `CANCELLED`,
`EXPIRED`, `REJECTED`, `EXECUTION_FAILED` and `UNCERTAIN`.

| Step | Authority |
|---|---|
| Buy | Requires an authenticated buyer session with a wallet paired to that session. It creates an idempotent reservation. |
| Quote | The service rescans both wallets at the tip, checks inventory against the seller's spendable balance and all reservations, checks the buyer's funds, picks an unspent mature sponsor coinbase (`getaddressutxos` plus a coinbase txid check), and runs the pinned `prepare-sale` with the paired receiving addresses. Terms are checked field by field before `attach_quote`. |
| Confirm | The buyer echoes the server digest. Any other digest, buyer, pairing or an expired quote is rejected. This is a purchase request, not a signature. |
| Proof | Fresh `native_zec_v2.js generate`, then `verify`. A rejection ends the order before the coordinator or any socket exists. |
| Authorize | Checks that the tip is unchanged, the before-scans are at the tip, the terms, trust and authorization hashes are unchanged, and both signers' non-signing `review` passes. |
| Execute | Starts only when both paired companions are online. A single-execution guard (`order_executions`, unique per order) applies. The pinned `execute-live` re-verifies the Groth16 authorization before opening its socket and again before assembly. |
| Approve | Each companion runs its wallet's own signer. Human mode uses `authorize-interactive`, which reads `APPROVE <digest>` from that process's `/dev/tty`. The service records an approval only from that companion's attestation plus a signer line that matches the exact role, digest and effects. |
| Settle | Coordinator receipt → independent RPC block membership and transaction size check → after-scans that must match the exact effects (otherwise `UNCERTAIN`) → receipt → 110 restart-durability blocks, checkpoint and rescan. |

## 2. Supported wallet pairing and the trust boundary

**Method: operator-confirmed local pairing** (`OPERATOR_CONFIRMED_LOCAL_COMPANION`).
1. The companion gets its own receiving address from its wallet.
2. It shows a 10-character one-time code that is valid for 5 minutes. The ledger stores only its hash.
3. The signed-in user enters the code in the browser. The role must match.
4. The companion shows the claiming account, and its operator types `PAIR`.

The pairing binds that account, its portal session hash and the wallet
address for up to 4 hours. Pairing again revokes any earlier pairing for the
same account or wallet.

It proves that the browser user can see and type into that companion's
terminal. **It is not cryptographic wallet login**, and spend authorization is
not used as login. The companion has no network listener and accepts no
browser input. It reads the owner-only ledger and writes only its own
evidence. The browser never receives a seed, key, signed group or raw
transaction.

`HUMAN_APPROVED` is attested by the launcher path: the companion runs only
`authorize-interactive`, only in an interactive terminal, and the service
refuses an attestation whose mode differs from the guarded execution. It is
not a cryptographic proof that a person typed the line.

## 3. Authentication and security boundaries

- Portal sessions gain a random `sid`. Pairings are bound to `sha256(sid)`.
  Every mutation requires a local `Origin`, a body of at most 2 KiB, and a
  per-session HMAC CSRF token.
- The website calls `python3 -m exchange.portal` with a fixed JSON shape on
  stdin. Actor, role and session come only from the signed cookie. There are
  no browser-supplied paths, commands, RPC methods or monetary digests.
- Buyer and seller see only orders they are party to. Issuer, auditor,
  anonymous and cross-role requests are refused.
- The service invokes wallets only for `address`, `balances` and `review`
  (non-signing), each under its existing sandbox profile with a scrubbed
  environment.
- Per-root `service-config.json` is owner-only. `SCRIPTED_TEST_MODE` is refused
  for the protected root.

## 4. Quote and pricing classification

Classification is `SELLER_FIXED_EXPERIMENTAL`. The seller sets it on the
seller desk, it is versioned, it applies only to new quotes, and the quote
binds payment = quantity × price. The seller's exact consent is the seller
wallet's approval of the digest. **"Exact ZEC quote verified" does not mean
"oracle-derived market price verified."** The oracle gate in
`phase4e-pricing-gate.md` is unchanged. The legacy 4E-B order is shown as
`OPERATOR_SELECTED_TEST_PRICE`.

## 5. Disposable SCRIPTED_TEST_MODE acceptance (real chain, real proof, real signers)

**Environment:**
- Root `/private/tmp/zwa4e-phase4e-c-scripted`, container `zwa-phase4e-c-scripted`, RPC 18331.
- Built with the 4E-A script: three new issuances and three scripted trades, followed by a real restart.

**Browser actions:**
1. The seller signed in, paired, offered 93 gold units and set 1,000 zatoshis per unit.
2. The buyer signed in (separate cookie origin), paired, clicked Buy, reviewed the quote and requested the purchase.

**Results:**
- Order `e2a33dc6f2730112a30f59fd1255fee2` passed through all 11 states.
- **Quote:** digest `98137b6e0810321f8dcb17990c367036398af3bec892631daf197852ccb6c2e3`, 1 unit for 1,000 zatoshis, target height 1115.
- **Groth16:** all five gates verified; trade commitment `2933036249247595044277108467226955886564545698790405704637783964712811932761`. Purchased-note lineage remains `UNSUPPORTED`.
- **Transaction:** `84f7ba5a539337c42984d6caf9d2c2e2e0bb983befc2351f0d9917121ca18b18`, included at height 1115 in block `30e4980a28b1d1beb4dc02ad2e9a2287e5587fa9d2b850208195fc8ed1ecd5ba`.
- **Wallet scans:** buyer gold 7 → 8 and native −1,000; seller gold 93 → 92 and proceeds 31,000 → 32,000.
- **Durability:** 110 blocks mined, anchor at 1125, and a fresh rescan. The browser portfolio showed 8 gold units with both purchase txids. The seller saw a −1 unit / +1,000 zatoshi receipt.
- **Restart:** a live stop and start dropped the volatile tip (1225 → 1126) and kept anchor 1125. The receipt still returned 200, the stale portfolio was hidden, and a rescan at 1126 matched.

**Live negatives on this stack:**
- 22/22 role-scoped HTTP checks passed: anonymous, issuer and auditor reads; missing CSRF, missing Origin and foreign Origin; another session's CSRF token; seller attempting to buy; buyer attempting to set a price; changed digest; cancel after settlement; seller cancelling a buyer's order; unknown pairing code; oversized body.
- **Disconnected companion:** order `886afddd…` reached `TRADE_AUTHORIZED` with no execution, then the buyer cancelled it before approval and the reservation was released.
- Fresh-proof adversarial vectors passed: 1 positive and 11 negative (payment, asset, receiver, seller proceeds, expiry, forged issuer, revoked root, stale authority, invalid proof, public input, replayed digest).

## 6. Protected chain state (zwa4e-phase4e-b)

**Before any change, read-only:**
- Genesis and the 1115 anchor matched, and the tip was 2116.
- All four accepted transactions were in their blocks.
- The newest scan was at the tip: buyer 8/5/3, seller 92/95/97, 32,000 zatoshis.
- All four SQLite databases passed `integrity_check`.

**Changes made:**
1. **Checkpoint:** `/private/tmp/zwa4e-phase4e-b-pre-4e-c-backup-20261012` (SQLite backup API, 243 files; live RocksDB not copied).
2. **Migration:** ledger v2 → v3; all rows and history kept.
3. **Pending reservation:** `f67b3ed74d74db8f3b6f6c74f3fff106` was created by the authenticated `buyer` account. It had no quote, no proof, no signed group (both signer journals list only the four earlier digests), no settlement and no execution. It was cancelled through `close_before_approval`, and event history now runs 1 → 2.
4. **Configuration:** `HUMAN_APPROVED` service configuration written.
5. **Idle check:** one service tick left the tip at 2116 and the legacy order untouched.

No protected transaction, signature or mining has occurred in Phase 4E-C.
**No new transaction ID, wallet effect or receipt exists yet on the protected
chain.** They will exist only after the owner's approvals.

## 7. Files

**Created:**
- `exchange/config.py`, `tools.py`, `order_service.py`, `wallet_companion.py`, `portal.py`, `order_paths.py`, `test_phase4e_c.py`
- Website: `lib/exchange-route.ts`, `lib/order-labels.ts`
- Components: `BuyPanel`, `OrderLive`, `Participant`, `SellerListing`, `SellerPricing`, `WalletPairing`, `client-api`
- Routes: `market`, `wallet`, `wallet/unpair`, `offers/[id]/price`, `orders/[id]/confirm`
- Pages: `/exchange/wallet`, `/exchange/orders/[id]`
- This report and the runbook

**Modified:** `order_ledger.py` (schema v3), `portfolio_scans.py`, `test_order_ledger.py`, `phase4e-a-scripted-validation.sh`, `test_phase4e_order_adversarial.js`, the auth and bridge libraries, the order, asset and seller pages, the shell navigation, CSS, and the existing exchange routes.

**Deleted (preserved in git history):** `exchange/order_execution.py`, `order_view.py`, `order_mutation.py`, `checkout/operator/phase4e-b-complete-gold-order.sh` (one-shot; refuses to rerun), `website/components/exchange/OrderRequest.tsx`.

No dependency changes. Frozen circuits, Groth16 pins, the pinned tx-tool and `PRE-EXISTING ZK-ORIGIN` sources are unchanged. All Phase 4E-C code is `NEW ZWA PROTOCOL WORK`.

## 8. Tests

- **Python:** 57 passed. That is the original 25 (two ledger lifecycle tests updated for the now-mandatory buyer confirmation) plus 32 new 4E-C tests covering:
  - pairing: role, expiry, single use, other process, session binding, re-pairing
  - quoting: price/pairing waits, insufficient inventory and funds, overlapping note commitments, wrong AssetBase, payment ≠ quantity × price, stale quote, confirmation binding
  - authorization and execution: invalid proof, review rejection, changed terms, disconnected companions, unavailable RPC, full lifecycle with durability, buyer and seller rejection, timeout, coordinator crash after both approvals, mode/pairing mismatch, missing receipt, scan mismatch, no release after submission, legacy receipt never mining
  - portal: unpaired purchase, cross-user and cross-role access, bridge shape and role checks, duplicate Buy
  - migration: v2 → v3, concurrent first open
- **Rust:** pinned `approval_is_exact_and_defaults_to_reject` passed.
- **Checks:** `tsc --noEmit` and `next build` passed. Python compile, `bash -n` and `git diff --check` passed.
- **Responsive:** no horizontal page overflow at 320, 390 or 1024 px on the order, asset, wallet, orders and portfolio pages. The window could not be resized, so pages were measured in same-origin frames.

## 9. Known limitations and release blockers

1. **The human-approved protected purchase is pending the owner.**
2. Pairing is local and operator-confirmed, not cryptographic wallet authentication. Approval mode is attested by the launcher path.
3. Prices are seller-fixed test prices. The oracle/NAV binding and versioned price statement remain blocked.
4. Note-level locking is approximated by serializing quotes per wallet, because the signers choose their own notes. Pinned Zebra does not enforce Action Group expiry; a disclosed group stays usable until its input notes are spent.
5. Restart durability is the pinned node's policy (blocks mined past its ~99-block volatile window), not consensus finality. The disposable stabilization record still names a scan directory from before the naming fix.
6. Issuer and credential authorities are disposable and self-generated. Rooted purchased-note lineage is unsupported. Experimental QEDIT ZSA only; not production mainnet.
7. The portal is local-only: one buyer and one seller account, no RFQ, and no issuer or auditor scopes for orders. The older 4C.1 adversarial script needs a 4C.1-specific root and was not re-run.
