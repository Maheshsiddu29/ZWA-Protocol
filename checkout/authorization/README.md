# Phase 4C.1 experimental checkout authorization

This is a new native-ZEC checkout relation. It does not modify the frozen Phase
0F provenance circuit, the Phase 1B eligibility circuit, or Halo2 baselines.
The Poseidon and Merkle components are **PRE-EXISTING ZK-ORIGIN** code carried
by the sibling `ZWA-Protocol` repository. The `CRED_V2` receiver-bound leaf
follows the Phase 1B work. This directory's combined Circom relation, quote
binding, gate, and operator scripts are **NEW ZWA PROTOCOL WORK**.

## Statement and binding

One Groth16 proof constrains both authorized issuance membership for the exact
32-byte AssetBase and active credential/policy membership for the buyer's exact
43-byte Orchard receiver. Its credential leaf contains the authority-approved
receiver commitment, using the Phase 1B `CRED_V2` domain. The public inputs
also include both active roots, both authority key commitments, the policy root,
AssetBase limbs, buyer receiver limbs, quote digest limbs, expiry height, and a
new V2 trade commitment.

The V2 commitment is Poseidon over the offered AssetBase commitment, approved
buyer receiver commitment, both halves of the Phase 4B quote digest, and the
transaction expiry height. The coordinator recomputes the Phase 4B BLAKE2b
quote digest from actual `PurchaseTerms` before it calls the SHA256-pinned
verifier. That digest covers quantity, native-ZEC price, both receiver
identities, sponsor input/change, sponsor network fee, **separate** matcher
fee, nonce, network, branch, target, and expiry. Native ZEC is represented by
the existing quote's native-payment tag and amount; no requested AssetBase is
fabricated. The current experimental checkout requires matcher fee zero.

The two disposable authorities sign canonical `ZWA1ROOT` root payloads with
Ed25519. The coordinator's private-home trust file pins their public keys,
exact active roots, versions, policy root, network, and quote. The verifier
checks signatures, validity windows, public inputs, and the Groth16 proof.
These are operator-provisioned regtest authorities, not production trust
anchors. Root signatures are generated in memory; authority private keys and
credential subject secret are never written to disk. The proving key is a
local development key and is unsuitable for production trust.

The Phase 4C.1 transaction-tool worktree is `/private/tmp/zwa4c1-tx-tool`,
locally committed at `16052323904be8efbb9e0f92d059fa57c9198f8a`.
Its `execute-live` checks authorization before opening the approval socket and
again after both signer messages, before transaction construction. Legacy
file-based `assemble` and `submit` routes are disabled in that binary.
The original Phase 4B tool and launchers remain pinned and unchanged.

## Operator commands

Set `PARAMS_DATA_HOME` to the parent of the trusted `ZcashParams` directory;
both launchers verify the existing Sapling parameter checksums. They never
download or regenerate those parameters.

```bash
cd /Users/mahesh/Desktop/Colosseum/ZWA/ZWA-Brand-Integration
PARAMS_DATA_HOME=/Users/mahesh/Desktop/Colosseum/ZWA/zsa-feasibility/.local/share \
ROOT=/private/tmp/zwa4c1-scripted-new PORT=18268 API_PORT=18745 \
CONTAINER=zwa-phase4c1-scripted-new SCRIPTED_TEST_MODE=1 \
bash checkout/operator/phase4c1-scripted-validation.sh
```

After the scripted run passes, the owner can run the following **in an actual
interactive terminal**. The buyer and seller each require a direct typed
approval in their own signer prompt. No scripted approval flag is accepted by
this launcher.

```bash
cd /Users/mahesh/Desktop/Colosseum/ZWA/ZWA-Brand-Integration
PARAMS_DATA_HOME=/Users/mahesh/Desktop/Colosseum/ZWA/zsa-feasibility/.local/share \
ROOT=/private/tmp/zwa4c1-human-new PORT=18269 API_PORT=18745 \
CONTAINER=zwa-phase4c1-human-new \
bash checkout/operator/phase4c1-human-demo.sh
```

Both roots, each credential, and each proof are generated from that run's
actual issuance, buyer receiver, and quote. No historical fixture authorizes
the purchase. The read-only API exposes the coordinator's verification result
for the exact session; the website shows each verified field separately from
wallet approval, settlement, confirmation, and unsupported Halo2 note lineage.

## Remaining boundaries

The Groth16 statement proves issuance and credential/policy memberships for
the pinned disposable roots and binds them to the exact quote. It does not
prove current wallet control; the buyer signer checks its own receiving
address and validates its own Action Group effects. The seller signer performs
the analogous proceeds check. Because experimental Action Group signatures do
not cover the full assembled transaction, the coordinator still supplies
application-level assembly and effect checks. The proof does not establish a
Halo2 relationship from the purchased output note to the authenticated chain.
The API's authorization file is a local coordinator output, not an independent
browser verifier.
