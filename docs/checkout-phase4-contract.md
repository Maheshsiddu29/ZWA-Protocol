# Conditional Phase 4 checkout contract

This is a design gate, **not an implemented API**. Phase 3.5 found no accepted
fee-funded ZSA-for-ZEC purchase on the pinned tx-tool. The Phase 3 HTTP API
has no checkout routes. Clients must keep purchase actions disabled until the
requirements in `checkout-phase35-feasibility.md` are met and tested.

## One-screen buyer journey

1. Choose the one supported sample asset and fixed quantity.
2. Review a compact quote with the asset, quantity, total ZEC debit,
   separately itemized network and matcher fees, receiving wallet, and expiry.
3. Approve the exact effects once with a compatible signer.
4. See transaction submission, accepted-chain observation, finality, and
   receipt update automatically. Only a validated final receipt may say the
   asset was received.

Technical evidence can appear in an optional details panel. The buyer never
selects anchors, nullifiers, proofs, ChainRefs, memos, or manual fee transfers.

## Proposed backend operations and trust boundary

- `createQuote`: operator-pinned regtest network and single asset; canonical
  integer base units; immutable expiry and maximum total; no arbitrary
  transaction commands.
- `preparePurchase`: verify fresh seller input, buyer funding, authorized
  issuance where supported, recipient control, and the quote digest. A failure
  leaves the quote unprepared.
- `approveWithSigner`: an external-compatible signer sees the exact asset,
  amounts, recipients, fee ceiling, expiry, and complete transaction effects.
  A disposable local test signer is allowed only as `TEST-SIGNER MODE`; the API
  must not receive private keys in JSON or logs.
- `submitPurchase`: recheck expiry, input freshness, digest, signatures,
  proof, conservation, and fee bounds immediately before one atomic broadcast.
- `getPurchaseStatus`: derive status from trusted transaction and chain
  observations, never a caller-provided accepted flag. After restart, recover
  from authenticated durable evidence or return unknown/pending.
- `getPurchaseReceipt`: publish separately the observed transaction, lineage,
  policy, and finality verdicts. Do not include witnesses or secret notes.

These names are conceptual. Exact routes, wire bytes, transaction version,
recipient encoding, and quote digest are deliberately **unassigned** until
the successful regtest purchase fixes them against the actual builder. No
signature or quote format should be inferred from this document.

## Quote and state invariants

The quote needs an ID, pinned network ID, canonical 32-byte `AssetBase`,
asset quantity, native-ZEC payment amount, recipient binding, network fee,
matcher fee, maximum total cost, expiry, seller-order binding, transaction
version, and quote digest. All financial fields use checked integer arithmetic
in protocol base units. The displayed total equals the maximum authorized
buyer debit. The signer must reject a substituted asset, recipient, seller
order, fee, price, or transaction effect. No indefinite approval is permitted.

The candidate lifecycle is `QUOTE_READY`, `AWAITING_APPROVAL`, `PREPARING`,
`SUBMITTED`, `ACCEPTED`, `PENDING_FINALITY`, `FINALIZED`, `FAILED`, `EXPIRED`,
and `INVALIDATED_BY_REORG`. `ACCEPTED` records validated inclusion; it is
not asset receipt. `FINALIZED` requires the configured K=3 policy and a
validated asset/payment observation. Reorg invalidates prior confirmation.

## Receipt fields and independent verdicts

The receipt needs quote ID, network ID, transaction hash, block height/hash,
asset and ZEC payment identifiers, actual fee values, authenticated output
reference, confirmation state, rooted-lineage verdict, matcher permission,
eligibility, settlement status, verifier identity, and evidence hashes. Its
distinct verdicts are `TRANSACTION_ACCEPTED`, `LINEAGE_VERIFIED`,
`POLICY_ELIGIBILITY_VERIFIED`, and `SETTLEMENT_FINALIZED`; none implies the
others. Where the frozen V2 relation cannot prove the received swap output,
return the existing `UNSUPPORTED` verdict with a specific reason while
retaining genuine settlement evidence. Eligibility remains `NOT_EVALUATED`
until an actual policy gate is evaluated.

## Gates before enabling any route

1. Reproduce one positive-network-fee ZSA-for-ZEC transaction on the pinned
   Zebra regtest, with separate buyer/seller authorizations and scanned
   effects. Record its txid and block hash.
2. Enforce expiry, quote digest, exact fee/recipient bounds, funded inputs,
   replay prevention, and mutation rejection, including the complete negative
   matrix in the Phase 3.5 brief.
3. Validate the new chain's block linkage, transaction inclusion, Orchard
   roots, issuance and Action membership, history, and K=3 finality. Resolve
   the pinned ZIP-227 issuance-authorization/rho gap through an approved
   consensus baseline or verifier-side per-asset audit.
4. Test the resulting Action shape against frozen `NormalLineageStepV2`.
   Preserve a truthful unsupported verdict for split/reference successors.
5. Run the Phase 3 source pins, 236-case differential accounting, HTTP and
   Postman regressions before adding any frontend.
