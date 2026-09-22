# ADR 0004: Credential-approved receiver binding

- **Status:** Accepted for Phase 1B
- **Decision:** Commit the authority-approved Orchard receiver into the active credential leaf, and require the eligibility proof to use that same receiver for the trade's recipient commitment.

## Context

The Phase 0G eligibility statement bound a credential subject, a receiver, a policy tuple, and `TradeCommitmentV1` to one another, but the credential authority never approved a particular receiver. The credential leaf committed the authority, the subject commitment, the private class and jurisdiction, the credential expiry, and a nonce — and nothing about a receiver.

That left a real gap. Anyone holding the subject secret could build a fresh, fully valid eligibility proof naming a receiver of their own choosing. A lent or leaked credential secret therefore authorized delivery to an arbitrary wallet, even though every other check passed. The pre-Phase-1B circuit accepted exactly that substitution.

## Decision

The active credential leaf becomes:

```text
SubjectCommitment = H(SUBJECT1, subjectSecret)
CredentialMeta    = H(CREDMETA, investorClass, jurisdiction, credentialExpiry)
CredentialLeafV2  = H(CRED_V2, credentialAuthorityCommitment, SubjectCommitment,
                      CredentialMeta, credentialNonce, ApprovedReceiverCommitment)
```

`ApprovedReceiverCommitment` is the existing canonical `H(RECEIVR1, limb0, limb1, limb2)` over the canonical 43-byte raw Orchard address. Phase 1B deliberately introduces no second receiver encoding; reusing one commitment is what makes the circuit equality meaningful.

The eligibility circuit does not take the approved receiver as a separate input. It computes the receiver commitment once and consumes that single signal twice: once inside the credential leaf whose Merkle membership is proved against `activeCredentialRoot`, and once inside `H(RCPBIND1, SubjectCommitment, ReceiverCommitment)` which feeds `TradePartB`. Equality is therefore structural rather than an added constraint, so there is no separate signal that could be left unconstrained and no host-side comparison to skip.

A new leaf domain `"CRED_V2"` (18949280892933682) replaces `"CRED_V1"` (18949280892933681). The leaf statement changed, so a distinct domain keeps the two unambiguous: a V1 leaf can never be reinterpreted as a V2 leaf.

## Phase 1B invariant

An active eligibility credential authorizes exactly the Orchard receiver committed into its credential leaf. A valid eligibility proof must prove that the authority-approved receiver commitment in the credential leaf equals the receiver commitment used in the trade's `RecipientCommitment`.

```text
credential(receiver A) + trade(receiver A)  -> valid
credential(receiver A) + trade(receiver B)  -> invalid
```

This holds even when the subject secret is valid, the investor class and jurisdiction are correct, the credential is unexpired, the active credential root is otherwise valid, and `TradeCommitmentV1` is structurally well formed.

## Authority approval is not wallet control

These are two different properties and must not be conflated:

1. **The credential authority approved receiver A.** This is what Phase 1B implements, and it is an issuance-time statement.
2. **The trader currently controls receiver A.** This is a live authentication property. It is not implemented here and remains a later matcher concern; see the recipient-control challenge in `architecture.md`.

Phase 1B narrows delivery to an authority-approved receiver. It does not prove that whoever is settling holds that receiver's spending key.

## Consequences

`TradeCommitmentV1` is unchanged: its algorithm, field ordering, domains, staging, and both frozen golden values reproduce exactly, because the credential leaf is not an input to it. The `AssetBase` encoding, the Orchard receiver encoding, the root-envelope serialization, and the replay lifecycle are likewise untouched.

The credential leaf hash and every active credential root derived from it do change, which is intended. Migrated Phase 0G evidence keeps its original V1 leaf and root and stays reproducible; Phase 1B evidence lives alongside it in `tests/fixtures/phase1b-eligibility-v2.json`.

A credential authority must now decide a receiver at issuance time. A subject who legitimately needs to settle to more than one receiver requires one credential leaf per approved receiver, which is an operational consequence the authority controls.
