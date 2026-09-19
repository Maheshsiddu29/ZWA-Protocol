# Threat Model

Each defense is scoped to the frozen MVP. “Circuit” means a relation proved by the migrated Groth16 circuit; “matcher” means application enforcement outside Zcash consensus.

## Fake AssetBase
**Attack:** Substitute an unauthorized or look-alike asset identifier.  
**Current defense:** Lossless canonical `AssetBase` limbs are included in the authorized issuance leaf and trade commitment; mutation tests fail.  
**Enforcement layer:** Provenance circuit and matcher recomputation.  
**Remaining limitation:** Issuer-root authenticity depends on matcher configuration and signatures.

## Forged issuer root
**Attack:** Present a tree root containing attacker-created issuance.  
**Current defense:** Matcher verifies the root envelope signature against an approved issuer key.  
**Enforcement layer:** Matcher.  
**Remaining limitation:** Signature-envelope code is planned, not implemented; the circuit does not authenticate issuers.

## Stale issuer root
**Attack:** Reuse a valid old root after withdrawal or correction.  
**Current defense:** Version and validity metadata plus matcher expected-current-root policy.  
**Enforcement layer:** Matcher.  
**Remaining limitation:** Root distribution and rollback policy need implementation and operations review.

## Fake credential
**Attack:** Fabricate eligibility attributes or a credential leaf.  
**Current defense:** Active-root Merkle membership and constrained credential commitment.  
**Enforcement layer:** Eligibility circuit plus matcher-authenticated root.  
**Remaining limitation:** Authority key compromise defeats authenticity until rotation.

## Borrowed credential
**Attack:** Use another subject's credential for the attacker’s receiver.  
**Current defense:** The subject secret and exact receiver jointly determine the recipient commitment; Phase 0G rejected the tested attack.  
**Enforcement layer:** Eligibility circuit.  
**Remaining limitation:** The matcher still needs a wallet control proof for the receiver.

## Wrong recipient
**Attack:** Replace the intended Orchard receiver.  
**Current defense:** Canonical receiver limbs are included in the recipient and trade commitments; substitution fails.  
**Enforcement layer:** Eligibility circuit and matcher recomputation.  
**Remaining limitation:** Correct encoding does not establish spending-key control.

## Recipient key not controlled by claimant
**Attack:** Claim eligibility while directing assets to an address the claimant does not control.  
**Current defense:** Required matcher challenge bound to nonce, session/domain, and expiry.  
**Enforcement layer:** Matcher and wallet authentication.  
**Remaining limitation:** Phase 0 did not implement or prove the exact wallet mechanism.

## Revoked credential
**Attack:** Spend using a credential removed from the active set.  
**Current defense:** Proof must verify against the matcher-selected active root; Phase 0G tested root rotation invalidation.  
**Enforcement layer:** Eligibility circuit and matcher.  
**Remaining limitation:** Revocation latency follows signed-root publication and matcher update latency.

## Stale credential root
**Attack:** Supply a formerly valid active root.  
**Current defense:** Signed version, validity metadata, freshness checks, and configured current root.  
**Enforcement layer:** Matcher.  
**Remaining limitation:** Globally synchronized revocation is not provided.

## Wrong credential authority
**Attack:** Use a root signed by an unapproved authority.  
**Current defense:** Matcher checks the signer against the single approved authority configuration.  
**Enforcement layer:** Matcher.  
**Remaining limitation:** Key onboarding and rotation procedures remain to be designed.

## Policy substitution
**Attack:** Replace the asset transfer policy with an easier policy.  
**Current defense:** The policy root is in the issuance commitment and `TradeCommitmentV1`; policy membership is constrained.  
**Enforcement layer:** Both circuits and matcher recomputation.  
**Remaining limitation:** Policy meaning and approved policy publication are application governance.

## Asset substitution
**Attack:** Prove one offered asset while settling another.  
**Current defense:** Both exact asset commitments are inside the shared trade commitment reconstructed by the matcher.  
**Enforcement layer:** Circuits and matcher/settlement adapter.  
**Remaining limitation:** The settlement adapter must preserve the canonical mapping into transaction actions.

## Amount substitution
**Attack:** Change offered or requested quantity after proof generation.  
**Current defense:** Both range-constrained amounts are committed; Phase 0F mutation tests failed.  
**Enforcement layer:** Circuits and matcher recomputation.  
**Remaining limitation:** UI and serialization layers require conformance tests.

## Payment-asset substitution
**Attack:** Replace the requested asset while keeping the RWA proof.  
**Current defense:** Requested `AssetBase` is losslessly committed into Trade Part A.  
**Enforcement layer:** Both circuits and matcher recomputation.  
**Remaining limitation:** Transaction construction must use the same canonical bytes.

## Fee-amount substitution
**Attack:** Reduce, remove, or increase the agreed ZEC fee.  
**Current defense:** Fee amount is in the fee and trade commitments; finalized action-group removal invalidated the complete Phase 0E transaction.  
**Enforcement layer:** Circuits, matcher, transaction authorization/binding.  
**Remaining limitation:** The fee remains voluntary outside the ZWA flow.

## Fee-recipient substitution
**Attack:** Redirect the matcher payment.  
**Current defense:** Matcher fee recipient commitment is included in `TradeCommitmentV1`.  
**Enforcement layer:** Circuits and matcher/settlement adapter.  
**Remaining limitation:** Commitment-to-Zcash-recipient mapping needs adapter conformance tests.

## Proof splicing
**Attack:** Combine provenance from one intent with eligibility from another.  
**Current defense:** Both public statements must contain the identical matcher-recomputed trade commitment; Phase 0G rejected splicing.  
**Enforcement layer:** Matcher gate.  
**Remaining limitation:** Verifier key selection and public-input parsing require hardening.

## Exact-commitment replay
**Attack:** Submit a second settlement for an already accepted commitment.  
**Current defense:** Planned atomic matcher state keyed by `tradeCommitment`, with terminal `CONSUMED` and `EXPIRED` states.  
**Enforcement layer:** Matcher persistence.  
**Remaining limitation:** Phase 0 deliberately did not prove single-use execution; durable state is unimplemented.

## Expired trade
**Attack:** Settle after the intent deadline.  
**Current defense:** Matcher compares current time to unsigned Unix-second expiry; credential expiry must cover trade expiry.  
**Enforcement layer:** Eligibility circuit and matcher.  
**Remaining limitation:** Trusted time source and clock-skew policy need definition.

## Malicious matcher
**Attack:** Ignore compliance, censor trades, leak private intent data, or construct a different transaction.  
**Current defense:** Traders must authorize spends and can verify transaction details; Zcash rejects invalid consensus transactions.  
**Enforcement layer:** Wallet authorization and Zcash consensus.  
**Remaining limitation:** MVP compliance is matcher-enforced; a malicious matcher can bypass application policy or harm privacy and availability.

## Malicious trader
**Attack:** Send malformed proofs, inconsistent fields, duplicate requests, or unauthorized spend attempts.  
**Current defense:** Canonical parsing, proof checks, root checks, replay locking, wallet authorization, and Zcash validation.  
**Enforcement layer:** Matcher, wallets, and Zcash consensus.  
**Remaining limitation:** Resource-exhaustion controls and abuse limits remain to be built.

## Direct ZSA transfer outside ZWA
**Attack:** Transfer an RWA ZSA without using the compliant matcher.  
**Current defense:** None at consensus level. ZWA can gate only transactions it constructs or relays.  
**Enforcement layer:** Application policy only.  
**Remaining limitation:** Arbitrary external transfers are not blocked; the MVP must state this plainly.

## Recipient-key compromise
**Attack:** An attacker controls the intended receiver’s spending key.  
**Current defense:** Standard wallet key protection and incident-driven credential/root updates.  
**Enforcement layer:** Wallet operations and credential authority.  
**Remaining limitation:** Receiver binding cannot distinguish the legitimate user from a key thief.

## Compromised issuer signing key
**Attack:** Sign malicious issuance roots as the approved issuer.  
**Current defense:** Operational key security, versioned roots, emergency key removal, and explicit matcher configuration.  
**Enforcement layer:** Issuer operations and matcher.  
**Remaining limitation:** No on-chain registry or circuit-level authority check exists.

## Compromised credential-authority key
**Attack:** Sign a root containing unauthorized credentials.  
**Current defense:** Operational key security, rotation, revocation, and matcher-approved key configuration.  
**Enforcement layer:** Authority operations and matcher.  
**Remaining limitation:** Trades accepted before detection cannot be retroactively made non-existent.
