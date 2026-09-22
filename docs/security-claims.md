# Security Claims

## Proved in Phase 0

- Experimental ZSA issuance, transfer, burn, and finalization on the pinned local stack.
- Experimental atomic ZSA-to-ZSA exchange.
- Shielded ZEC supplied by a trader and received by the matcher inside the same atomic transaction.
- Removing the finalized matcher action group invalidates the complete transaction.
- Lossless binding of the canonical 32-byte `orchard::note::AssetBase` through two 128-bit little-endian limbs.
- Authorized issuance Merkle membership and policy-root binding.
- Provenance and eligibility proofs bind to one exact `TradeCommitmentV1`.
- ZEC matcher-fee amount and recipient commitment are bound into the trade commitment.
- Private credential and policy membership with only the active root and trade commitment public.
- Credential subject and exact Orchard receiver determine the recipient commitment.
- An active credential authorizes exactly the Orchard receiver committed into its leaf, so the same credential cannot settle to a receiver the authority did not approve.
- Borrowed-credential and receiver-substitution attempts fail.
- Investor class and jurisdiction remain private while their permitted tuple is proved.
- Credential expiry must cover trade expiry.
- Active-root rotation invalidates membership under the new root.
- Proof splicing and tested trade-field mutations fail at the combined matcher gate.

## Not proved

- Production ZSA mainnet readiness or production security of the experimental branches.
- Consensus-enforced RWA compliance or restriction of arbitrary transfers outside ZWA.
- Complete recursive custody lineage or globally synchronized revocation.
- Issuer-root authentication inside the provenance circuit.
- Credential-root authority or freshness inside the eligibility circuit.
- Mandatory global protocol taxation or consensus-level matcher fees.
- A production trusted setup or production key management.
- Independent-party signing and custody workflow.
- Bridge provenance, corporate actions, or regulator disclosure.
- Proof of recipient spending-key control inside either ZK circuit. Phase 1B proves that the credential authority approved a receiver, not that the trader currently controls it.
- Liveness, high availability, or Byzantine tolerance of a matcher network.

## Future work

Implement and audit canonical libraries, signed-root envelopes, root rotation policy, recipient-control authentication, durable replay transitions, independent signing, settlement failure recovery, reproducible circuit builds, production parameter governance, and operational monitoring. Any production claim requires fresh review of the underlying Zcash implementation and the complete integrated system.
