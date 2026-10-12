# Phase 4E-B owner review: existing gold order

**Verdict: PARTIAL / BLOCKED for the complete Exchange.** The specific existing
gold order reached a real proof-gated, scripted experimental settlement and a
durable private receipt. The browser still cannot independently prepare a new
executable quote, pair a human wallet, or complete a purchase without the
operator CLI. This is not final product acceptance.

## 1. Goal completed

Order `ec27f52d2e7ad69287f7d4d4357025af` progressed from `QUOTE_READY` through
`PROOF_VERIFIED`, `TRADE_AUTHORIZED`, `BUYER_APPROVED`, `SELLER_APPROVED`,
`SUBMITTED`, `INCLUDED`, `RECONCILED`, and `RECEIPT_AVAILABLE`. The two approvals
were independent disposable signer invocations in **SCRIPTED_TEST_MODE**. No
human approval occurred. The original three purchases and their AssetBases
remain on the same experimental chain.

The new transaction is
`0c9ad1ad369e6c72bf435329c67ef6693057b158857b5a8f0c7eca901056430c`,
included at height 1115 in block
`8d167ce8408e23d19937f410b2512d2ae4285bd8bb6050696f7eed77d9fe81d6`.
The exact QuoteV1 digest is
`acc0dc68bc573e71e72e076b3b1ec83455cf0186587b373ed9e0b98bdafa07c0`:
one gold-reference unit for 1,000 zatoshis, with a separate 5,000-zatoshi
network fee paid by the disposable sponsor. This 1,000-zatoshi price is an
operator-selected **test** price, not an oracle or NAV-derived price. The proof
verifier checked the issued AssetBase, buyer receiver, exact quote digest,
issuer and credential root signatures, policy, and Groth16 public inputs.
The coordinator independently verified the authorization before and after
receiving signer groups and submitted the actual transaction.

After two node restarts, the live chain tip is 2116 and its durable anchor is
the accepted height-1115 block above. Fresh independent scans show:

- Buyer: 8 gold-reference, 5 Treasury-style, 3 credit units; 624,968,000 native zatoshis.
- Seller: 92 gold-reference, 95 Treasury-style, 97 credit units; 32,000 cumulative native zatoshis.
- The new gold trade changed buyer/seller gold units by +1/-1 and native balances by -1,000/+1,000 respectively.

## 2. Files created

`exchange/order_execution.py`, `exchange/order_mutation.py`,
`exchange/order_view.py`, the two Phase 4E-B operator scripts,
`checkout/authorization/test_phase4e_order_adversarial.js`, the private-order
Next.js bridge, request component, and scoped API routes. Runtime evidence and
both backups are private under `/private/tmp`; no wallet secret or database is
in Git.

## 3. Files modified

`exchange/order_ledger.py`, its tests, `exchange/persistent_chain.py`, its
tests, `exchange/portfolio_scans.py`, and the buyer orders, activity, portfolio,
and seller pages.

## 4. Files deleted

None. The failed first preflight was renamed and retained privately; its failure
preceded proof generation or transaction submission.

## 5. Architecture changes

The SQLite ledger migrated from schema 1 to schema 2 without losing prior
offers, reservations, or event history. It now records ordered transitions,
required evidence, unique txid settlement, and idempotent replay. The trusted
operator adapter verifies live chain identity, exact quote, wallet scans,
Groth16 authorization, signer observations, coordinator receipt, independent
block inclusion, and wallet balance deltas before advancing. The application
offers participant-scoped read APIs for orders, receipts and available offers,
plus authenticated offer creation, purchase request and safe preapproval
cancellation. The browser request reserves inventory; operator proof and wallet
signing remain separate.

`PRE-EXISTING ZK-ORIGIN` Poseidon/Merkle primitives and frozen research sources
were not modified. These Phase 4E changes are `NEW ZWA PROTOCOL WORK`.

## 6. Security impact

Buyer and seller HTTP reads require both a signed role session and the exact
provisioned participant ID. Issuer, auditor, uninvited and anonymous sessions
cannot read the private order or receipt. The HTTP mutation bridge has fixed
actions, strict JSON shape, local origin checking, and no wallet signing or raw
Zebra RPC surface. The test signers keep seeds outside the browser. A wrong
digest was rejected by an isolated signer copy with no authorization output.

The proof's issuer and credential roots are **disposable self-generated test
authorities**. They do not prove external issuer standing or real-world asset
backing. QuoteV1 binds the native payment amount but does not bind an external
market snapshot or NAV derivation. The known experimental Action Group signature
scope and expiry qualifications remain. Rooted note lineage is `UNSUPPORTED`.

## 7. Dependency changes

None in the repository. Existing pinned Zebra image and pinned transaction
tool revision were used. The built-in `snarkjs`/Groth16 material was unchanged.

## 8. Tests run

- Python combined suite: 25 tests, including concurrency, idempotency, replay,
  quote mismatch, ledger lifecycle, safe anchor regression, and checkout API.
- Current real authorization: one positive and 11 negative vectors (payment,
  asset, receiver, seller payout, expiry, forged/revoked/stale roots, proof,
  public trade input, replayed digest).
- Pinned Rust signer test `approval_is_exact_and_defaults_to_reject` passed.
- Isolated copied-wallet signer rejected a wrong exact digest, exit 2, with no
  authorized group and no approval socket. The copy was then removed.
- Actual node interruption: receipt endpoint returned 404 and buyer portfolio
  hid saved holdings while Zebra was stopped; the exact receipt returned after
  restart and a new combined wallet rescan passed.
- Local HTTP smoke used separate buyer, seller, issuer, auditor, uninvited and
  anonymous accounts. Buyer and seller saw 200 for the order and receipt;
  other roles received 403/404. An idempotent replay returned the existing
  order; changed quantity, wrong role, missing Origin and post-settlement
  cancellation were rejected.
- In-app browser walkthrough: buyer login, 8-unit gold portfolio with both
  gold transaction IDs, full order timeline, quote and live receipt. The order
  page also displayed funded offers and clearly marked purchase requests as
  reservations pending operator quote and independent approvals.

## 9. Test results

All final tests above passed. The first execution attempt stopped at a scanner
log-size guard before any proof or transaction. The second completed real
settlement. Its first restart exposed an overly aggressive operator checkpoint:
height 2215 was a volatile tip and pinned Zebra dropped 99 recent empty blocks.
The original height-113 anchor and all four accepted transactions, including
the new height-1115 transaction, survived. An audited recovery changed only the
local operator anchor to the now 1,001-deep accepted block. `checkpoint` now
anchors at least 100 blocks below the observed tip. A second stop/restart
preserved this stable anchor and the receipt without recovery.

Private backups:
`/private/tmp/zwa4e-phase4e-b-owner-approved-backup-20261012` before execution
and `/private/tmp/zwa4e-phase4e-b-posttrade-backup-20261012` after execution.
SQLite databases in each were copied with the SQLite backup API. These backups
contain disposable wallet secrets and must stay private.

## 10. Lint and static checks

`npm run build` passed with Next.js type checking. Python compilation, shell
syntax checks, and `git diff --check` passed.

## 11. Git diff stat

See `git diff 2dc0c3e --stat` in the owner review output. Runtime databases,
wallet seeds, proof artifacts, and receipts are intentionally excluded.

## 12. Git status

The branch is `integration/phase4e-persistent-exchange`; see final review
output for the exact HEAD and clean status. No remote Git state changed.

## 13. Known limitations and next risks

The completed gold purchase was launched by the fixed operator script, then
observed in the application. A browser user can create a funded reservation but
cannot yet obtain a fresh executable quote, complete eligibility issuance,
pair an independently controlled wallet, or approve a transaction in the
application. The full normal purchase path, human approval, price provenance
binding, issuer/auditor scopes, institutional RFQ, and seven remaining asset
categories are unfinished. A ledger reservation still does not lock Zebra
notes; the signer and coordinator must recheck spendability at execution.
The portal is local-only and its role login is not wallet possession proof.

The next owner gate should review the real settlement evidence and these gaps
before continuing Phase 4E. Do not claim `COMPLETE_EXPERIMENTAL_EXCHANGE`.
