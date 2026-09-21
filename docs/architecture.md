# ZWA Protocol Architecture

## 1. Overview

ZWA Protocol is a privacy-preserving protocol for compliant shielded real-world assets on Zcash. Its MVP combines two off-chain compliance proofs with an experimental atomic ZSA settlement and a shielded ZEC matcher payment.

## 2. Problem

Regulated assets need evidence of authorized issuance and recipient eligibility. Public ledgers can expose asset holdings, counterparties, and transaction values. ZWA keeps the relevant attributes private while giving a configured matcher enough cryptographic evidence to decide whether to construct settlement.

## 3. Why privacy matters for tokenized assets

Institutional positions, transaction sizes, counterparty relationships, and eligibility attributes can be commercially or personally sensitive. The protocol therefore exposes commitments and proof results rather than raw credential class, jurisdiction, asset identifier, amounts, or recipient bytes at its compliance interface.

## 4. Why Zcash

The demonstrated experimental ZSA stack provides shielded custom-asset notes, nullifiers, value conservation, and atomic multi-action transactions. Native shielded ZEC can pay the matcher inside the same transaction. These properties were proven only on pinned experimental branches.

## 5. What ZWA Protocol does

ZWA proves that an exact offered asset is in an issuer-authorized set and that the exact intended recipient has an active credential satisfying the asset policy. Both proofs expose the same `TradeCommitmentV1`. The matcher independently recomputes it and constructs settlement only after all application checks pass.

## 6. Actors

- **Issuer:** defines the RWA series, commits authorized issuance, and signs root metadata.
- **Credential authority:** issues private credentials, maintains the active root, and signs root metadata.
- **Investor:** supplies a credential proof and authenticates control of the intended Orchard receiver.
- **Dealer/trader:** supplies the offered or payment asset and authorizes its spend.
- **Matcher:** verifies policy gates and coordinates the atomic transaction.
- **Experimental Zcash node:** validates and finalizes the shielded transaction.

## 7. Trust assumptions

Approved issuer and credential-authority keys are correctly configured. Their signing keys remain secure. The matcher faithfully applies the compliance and replay rules. Wallets protect spending keys. The pinned experimental stack supplies only the consensus and cryptographic behavior observed in Phase 0. Development Groth16 setup material is not production trust material.

## 8. Asset issuance

An issuance leaf commits the issuer, exact `AssetBase`, series, policy root, and issuance nonce. It is included in `authorizedIssuanceRoot`. The issuer signs a versioned root envelope. The circuit proves membership; the matcher authenticates the envelope and selects the expected current root.

## 9. Credential issuance

A credential leaf commits the configured authority, a subject commitment, private investor class, private jurisdiction, credential expiry, and nonce. The active credential tree supports revocation through root rotation. The authority signs versioned root metadata; the matcher checks signature, authority, freshness, and version.

## 10. Private RFQ flow

The parties agree on offered asset and amount, requested asset and amount, intended recipient, policy root, matcher fee, nonce, and expiry. The RFQ layer canonicalizes the intent and sends private witness data only to the component that needs it. The matcher receives proofs, public roots, the commitment, authenticated intent data needed for reconstruction, and settlement authorizations.

## 11. TradeCommitmentV1

`TradeCommitmentV1` commits both assets, both amounts, the recipient commitment, policy root, native ZEC fee amount and recipient commitment, nonce, and expiry using the exact Phase 0F/0G Poseidon staging. The frozen encoding is specified in [trade-commitment-v1.md](trade-commitment-v1.md).

## 12. Provenance proof

The provenance proof has two public inputs: `authorizedIssuanceRoot` and `tradeCommitment`. Its private witness proves that the exact offered `AssetBase` is part of an authorized issuance leaf with the same policy root and trade fields.

## 13. Eligibility proof

The eligibility proof has two public inputs: `activeCredentialRoot` and `tradeCommitment`. Its private witness proves active credential membership, allowed class and jurisdiction membership, and credential validity through trade expiry. The credential attributes remain private circuit inputs.

## 14. Recipient binding

A subject secret produces a subject commitment. The canonical 43-byte raw Orchard receiver is split into 128/128/88-bit little-endian limbs and committed. Their joint recipient commitment is placed in `TradeCommitmentV1`. This prevents proof reuse for another receiver but does not prove spending-key control.

## 15. Combined matcher gate

```text
AUTHORIZED ISSUER ──signed root──┐
                                 ▼
authorizedIssuanceRoot → PROVENANCE PROOF ──┐
                                             ├→ same TradeCommitmentV1?
activeCredentialRoot  → ELIGIBILITY PROOF ──┘            │
        ▲                                                  ▼
CREDENTIAL AUTHORITY ─signed root──→ MATCHER recomputes commitment
                                                           │
                              root/auth/control/replay checks all pass?
                                                           │
                                                           ▼
                                                   ALLOW SETTLEMENT
                                                           │
                                  ┌────────────────────────┼───────────────┐
                                  ▼                        ▼               ▼
                            RWA → investor       payment → dealer    ZEC → matcher
```

The gate requires valid signed roots, fresh versions, both valid proofs, identical public trade commitments, the matcher’s exact recomputation, unexpired state, recipient control, and acceptable replay state.

## 16. Root authentication

Issuer and credential roots travel in signed, versioned envelopes with validity metadata. The matcher verifies each signature against configured approved keys and checks the expected current version. Signatures stay outside the circuits for the MVP. There is no on-chain registry or DID framework.

## 17. Replay protection

The matcher stores one record keyed by `tradeCommitment`.

```text
CREATED → VERIFIED → SETTLEMENT_CONSTRUCTED → SUBMITTED → CONFIRMED → CONSUMED
   │          │                 │                  │
   └──────────┴─────────────────┴──────────────────┴→ FAILED
   └───────────────────────────────────────────────→ EXPIRED
```

- `CREATED`: the trade exists but has not passed current matcher verification.
- `VERIFIED`: root authentication and freshness, recipient control, expiry, both proofs, and the common trade commitment have passed current matcher verification.
- `SETTLEMENT_CONSTRUCTED`: a transaction candidate exists; the commitment is locked against concurrent construction.
- `SUBMITTED`: one candidate was submitted; track its txid and outcome.
- `CONFIRMED`: configured chain confirmation condition passed.
- `CONSUMED`: terminal success; all future attempts are rejected.
- `EXPIRED`: terminal policy rejection after expiry.
- `FAILED`: records a failed attempt and reason; an eligible retry restarts from `CREATED`.

Reject `EXPIRED` and `CONSUMED`. Use an atomic compare-and-set or database transaction to prevent duplicate construction. Construction alone never consumes a trade. A controlled retry may move `FAILED` back to `CREATED` only while unexpired, after reconciling the prior txid and under a bounded retry policy. The matcher must repeat all verification before moving the trade to `VERIFIED`. Mark `CONSUMED` only after configured confirmation.

## 18. Recipient-control authentication

The investor supplies the intended receiver. The matcher returns a challenge containing a fresh nonce, application/session domain, and expiry. A supported wallet proves or signs control; the matcher verifies it before accepting the eligibility proof for that receiver. The exact compatible Zcash wallet mechanism remains an implementation task and must receive separate review.

## 19. Atomic settlement

After the gate passes, the matcher constructs one experimental Zcash transaction whose action groups exchange RWA for the payment asset and pay the matcher in shielded ZEC. Phase 0 showed that the transaction-level authorizations and binding signature invalidate the whole transaction when the finalized matcher action group is removed.

## 20. ZEC matcher economics

The fee is denominated in native ZEC zatoshis. Its amount and matcher recipient commitment are inside `TradeCommitmentV1`, while its value transfer is an action group in the same transaction. This is an application-agreed matcher fee, not a mandatory consensus-level protocol tax.

## 21. Failure scenarios

An unauthorized `AssetBase` fails provenance. A valid asset paired with an ineligible recipient fails eligibility. Any mutation to committed assets, amounts, policy, recipient, fee, nonce, or expiry changes the reconstructed commitment. Invalid roots, signatures, freshness, recipient control, replay state, proof verification, or transaction consensus cause a block before or during settlement without a partial application-level success.

## 22. Security boundaries

Zcash enforces note ownership, nullifiers, value conservation, proof validity, and transaction atomicity. Circuits enforce the witnessed membership, policy, expiry, and commitment relations. The matcher enforces root authenticity, freshness, proof pairing, replay, fee policy, recipient control, and construction. Transfers made outside ZWA are beyond matcher enforcement.

## 23. Experimental dependencies

The MVP depends on pinned QEDIT `zcash_tx_tool`, Zebra, librustzcash, and Orchard revisions. These branches implement experimental ZSA, ZIP-227, and ZIP-228 behavior and are not production mainnet dependencies. The Groth16 setup used in Phase 0 was for development.

## 24. MVP scope

Build one RWA example, one issuer, one credential authority, signed-root checks, a private RFQ, the frozen commitment, both proofs, combined gate, replay state, recipient-control authentication, an atomic experimental settlement with shielded ZEC fee, deterministic fixtures, adversarial tests, and a clear demo. Exclude bridges, Ethereum, AMMs, order books, partial fills, recursion, generic policy languages, multiple authorities, and production matcher networking.

## 25. Future architecture

Future work may harden custody and independent signing, root distribution and revocation, setup governance, failure recovery, monitoring, and production deployment. Corporate actions, disclosure mechanisms, richer policies, multiple authorities, and recursive lineage require explicit scope approval and new security analysis.

## Work disclosure

**Pre-existing team technology:** ZK-ORIGIN Poseidon, Merkle, comparator, validator, and constants helpers and prior design work.

**Phase 0 Colosseum feasibility work:** ZSA lifecycle and swaps, ZEC matcher fee, provenance adapter, `TradeCommitmentV1`, eligibility proof, recipient binding, and combined gate.

**ZWA Protocol product work:** clean integration, matcher, RFQ workflow, root authentication, replay protection, settlement adapter, frontend/demo, and production-oriented testing and documentation.
