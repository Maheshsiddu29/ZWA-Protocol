# Phase 0 Feasibility Evidence

This is an evidence index, not a production-readiness claim. Raw logs, wallet state, proving keys, and development ceremony artifacts remain outside this repository.

## Experimental Zcash stack

The lifecycle gate used `QED-it/zcash_tx_tool` commit `6bcf2c5e679916b9297113b533ebc6df94a0e5b6` with the CI-pinned QEDIT Zebra commit `8c9c93fdd91b89fab387ec68362a93b2baca7bab`. Issuance and the issue/transfer/burn/burn lifecycle passed on the local pinned node; the public endpoint had rejected the otherwise constructed issuance block.

The swap and fee gates used these exact experimental revisions:

- `QED-it/zcash_tx_tool`, branch `zsa-swap`, commit `217b979ee01afb844190a162fb77874135aef587`
- `QED-it/zebra`, branch `zsa-swap-multiple-ag`, commit `0aef55cea41b83f17610e6ea708e59995f9e739f`
- `QED-it/librustzcash`, revision `5a55da948498dd0995d0f438b4c8e9a3f0150154`
- `QED-it/orchard`, revision `d91aaf146364a06de1653e64f93b56fac5b3ca0f`

These ZIP-227/ZIP-228 and ZSA capabilities are experimental. They are not generally available production Zcash mainnet features.

## Verified results

- ZSA lifecycle: **PASS**
- Atomic shielded ZSA swap: **PASS**
- Shielded ZEC matcher payment: **PASS**
- Finalized-fee mutation rejection: **PASS**
- Authorized issuance provenance: **PASS**
- Exact trade binding: **PASS**
- Private recipient eligibility: **PASS**
- Credential and recipient attack tests: **PASS**
- Proof-splicing test: **PASS**

Phase 0E produced transaction `c548fed477fd8a243fd6e2fbb2e82491758496a78a3eb0ca2112b4a1d8cfe279` at height `104`. The matcher’s wallet-scanned shielded ZEC balance rose from 0 to 5 zatoshis. Removing the finalized matcher action group produced an invalid signature; the candidate block was rejected, height remained 104, and balances remained unchanged. No Zcash consensus code was changed for that experiment.

Phase 0F used ZK-ORIGIN commit `eae3f3e19dd63562364149ecb000f2de7296d025`. Its provenance circuit had 8,837 constraints and verified authorized issuance, canonical `AssetBase` binding, policy-root binding, exact trade fields, and the ZEC fee condition. Phase 0G’s eligibility circuit had 13,502 constraints and verified private credential and policy membership, credential expiry, subject/receiver binding, root rotation behavior, and combined matcher gating. Both used development Groth16 trusted setups.

## New ZWA Protocol logic

ZWA adds the application protocol around those feasibility results: the exact commitment schema, provenance and eligibility adapters, signed-root authentication, recipient-control authentication, replay state, proof gating, RFQ coordination, and a settlement adapter. Phase 0 demonstrated selected proof and gate behavior; this clean repository does not yet implement the full application.
