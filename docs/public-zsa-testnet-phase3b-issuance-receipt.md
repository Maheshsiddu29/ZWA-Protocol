# Phase 3B public ZSA issuance receipt

Observed 2026-10-13 01:31–01:35 UTC on `integration/public-zsa-testnet`.
**Status: INCLUDED ON THE EXPERIMENTAL PUBLIC ZSA TESTNET; two observed confirmations.**
This is **NEW ZWA PROTOCOL WORK** using **PRE-EXISTING community tooling**.
It is neither Phase 4E atomic-purchase evidence nor Zcash mainnet functionality.

## Owner authorization and pre-submission check

The owner authorized **one** public issuance submission naming only canonical
node txid
`dfe0eed19c10cc0e6fad5f71227235ef5d08c232801a28e4a7ceafcbfc895340`.
Immediately before submission, the root-bound public check found chain `test`,
branch `77190ad8`, `/Zebra:5.0.0/`, genesis
`03536e258babad397d59ce2995d9015d0791ac5272b8348e8f07f94143a72bb7`,
block 1 `01747c32a6bd1bbfe5d91566d26c190719181908fa7560d9ef89da2204e82274`,
and fixed TLS endpoint `https://zsa.methyl.cc`. Tip was height **202120**,
hash `00034ba3b3fd9998a72e39fa37c0c11befc0f85bacb1d01f4c8505790bc0b1b4`.
The approved transaction expired at **202140**, leaving 20 blocks to expiry.
Its height-202100 Orchard anchor still matched the active block and tree state.

The faucet funding transaction
`f531d5fa5ee97c21ea36edd044312fd91c32095a3aedb854f7878a8ab5f37113`
remained in active block **202078**, hash
`0298685122810cf643d7f725698a429116b38564f7062179d7dfd8710432571a`,
with **43 confirmations** at the pre-submission tip. A wallet scan showed one
unlocked 1,000,000-zatoshi Orchard funding note, no asset, no pending transaction
and no spend row. The pending plan had no submission marker. This root was the
fresh, owner-only public root `/private/tmp/zwa-pub-public-phase3a-20261012`;
no Phase 4E root, key, wallet database or chain was used.

The exact signed transaction was **9,744 bytes**, SHA-256
`17959a23595283ee0a99bbbd952b2dca6a0712636a35abd33baddd105bccfef0`.
The pinned community Zebra parser accepted and reproduced every byte and
derived the approved canonical txid. It independently derived AssetBase
`a1fd01da3b709214fc48f01e42e09867f23652f1b56721ff1460a0765d4b3f14`,
one finalized issuance of **1,000,000 units**, and the exact 20,000-zatoshi fee.
The pinned Orchard verifier accepted the issuance authorization signature.
There was no transparent input, output or change and no unintended nonzero
asset or ZEC recipient. The known wallet-library txid differs because its V6
serialization clears `enableZSA` at byte 1738 (`0x07` to `0x03`); the original
bytes retain `0x07`, and the node parser's txid is canonical. The discrepancy
was explicitly checked, not normalized away. Details and pinned revisions are
in [the preparation report](public-zsa-testnet-phase3b-issuance-preparation.md).

## Submission and independent inclusion

The CLI's broadcast guard required the approved txid in the environment and
one interactive confirmation naming the same txid. It was submitted **once**
through the fixed public lightwalletd endpoint. The submission reply returned
the approved txid at tip **202120**, and a mode-0600 submitted marker was
created. No second submission was attempted.

An independent public RPC `GetTransaction` read returned the **same 9,744
bytes** and SHA-256, now at height **202121**. A separate compact-block read
found the same canonical txid at **index 1** in block
`0098a221eece2df7077ae6f0f7bfebc64fd76a64cab59b1357c55c0fc9276ec6`.
At the first observed tip **202121**, that block had **one confirmation**. A
fresh independent read at tip **202122**, hash
`02492f874922ee6cc5b05c06c24043a963aa1d2e63930944d8f39b3cfd1a84c4`,
found the same transaction in the same active block with **two observed
confirmations**. The node-derived AssetBase and issuance details were checked
against the included raw bytes. This is a point-in-time observation; later
reorganization remains possible. The saved public receipt records the initial
one-confirmation observation and is not silently rewritten.

The disposable issuer wallet rescanned and showed **1,000,000** units of
AssetBase `a1fd01da3b709214fc48f01e42e09867f23652f1b56721ff1460a0765d4b3f14`
(`finalized: true`) and **980,000 Orchard zatoshis**, with zero Sapling and
transparent ZEC. Funding **1,000,000 zatoshis − fee 20,000 = 980,000**;
the issued-asset inventory increased **0 → 1,000,000 units**. The name
`ZWA-TESTNET-OWNER-1` is an off-chain label; its on-chain description hash is
`b26671184fc43e90d9523a02e5f78896d7c15fe5a67be3bf03164ee845e77cc8`.
Issuer validating key:
`009fa50c4cd8ea5b878377fb2494de0252194ef08891a4c82232af9e2ad6556aaa`.

The privacy-checked public receipt is stored mode 0600 at
`/private/tmp/zwa-pub-public-phase3a-20261012/receipts/dfe0eed19c10cc0e6fad5f71227235ef5d08c232801a28e4a7ceafcbfc895340.1791855167.public.json`.
Its evidence class is `PUBLIC_CHAIN_INCLUSION`; the wallet observation is
separately labelled private and is not presented as public proof. The private
receipt, pending signed bytes and wallet database remain mode 0600 in the
owner-only root. No seed, spending key, viewing key, witness, receiver or raw
signed bytes entered the repository, website responses or this report. The
website public receipt list remains empty. No protected Phase 4E files changed.

## Review gate and limits

**PASS for one approved issuance submission and observed inclusion.** The
public chain and lightwalletd are operated by others, the node does not attest
its exact source revision, and two confirmations are not finality. This network
may reorg or reset. ZSA issuance is public by protocol; a later shielded
transfer's recipient and amount would require wallet evidence and must not be
represented as public observer data. ZWA Groth16 eligibility and atomic
purchase authorization were not applied to this issuance.

No transfer transaction was prepared or broadcast. The next gate is **separate
owner approval to prepare a transfer**. Any later broadcast would require
another approval naming that transfer's exact canonical txid.
There was no additional faucet claim, mainnet operation, deployment, push,
merge, PR or publication.

Local checks after inclusion: `cargo test --locked --offline` passed for all
14 public client tests and the node checker test. `git diff --check` and
whitespace checks on both new documents reported no errors. No code,
dependency or website file changed. The only local commit associated with
this phase remains `5ba8629` for the Phase 3A funding report; the preparation
and this receipt document are untracked pending owner review, following the
repository's no-commit-without-authorization rule.
