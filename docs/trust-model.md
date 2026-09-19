# Trust Model

## Issuer root

`authorizedIssuanceRoot` is authenticated by an issuer signature under a configured approved issuer key. The provenance circuit proves that the offered `AssetBase` belongs to the committed issuance set. The circuit does not authenticate the issuer, validate the signature, or decide which issuer keys are approved. Those checks belong to the matcher.

## Credential root

`activeCredentialRoot` is authenticated by a credential-authority signature under a configured approved authority key. The eligibility circuit proves credential membership and policy satisfaction. It does not authenticate the authority or establish freshness. The matcher verifies the signature, authority configuration, root version, and validity metadata.

## Matcher

The matcher is trusted to enforce the MVP protocol gate: canonical encoding, signed-root checks, root freshness, proof verification, equality of trade commitments, replay state, recipient-control authentication, fee policy, transaction construction, and submission. A matcher cannot forge Zcash consensus validity, spend notes without authorization, or make an invalid transaction valid. MVP compliance is matcher-enforced and can be bypassed by transfers constructed outside ZWA.

## Recipient control

Phase 0G binds a credential subject and a specific raw Orchard receiver to the trade, but it does not prove knowledge of that receiver's spending key. Before accepting a trade, the matcher must issue a challenge containing a nonce, session/domain binding, and expiry; the wallet must return a supported proof or signature of control; and the matcher must verify it before using that receiver in the eligibility statement. The exact Zcash wallet-authentication mechanism remains a Phase 1 research and implementation task.

## Zcash experimental stack

The prototype trusts only the cryptographic and consensus properties demonstrated by the pinned experimental QEDIT Zcash/ZSA stack. It does not assume production mainnet availability or security review beyond the observed feasibility evidence.

## Trusted setup

Phase 0 used development Groth16 trusted setups. They prove local feasibility only. Production parameters require a reviewed ceremony and operational plan; development keys must never be represented as production parameters.
