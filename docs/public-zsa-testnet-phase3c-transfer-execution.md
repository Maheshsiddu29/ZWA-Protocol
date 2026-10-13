# Phase 3C public shielded ZSA transfer execution report

Observed 2026-10-13 01:52–01:56 UTC on `integration/public-zsa-testnet`.
**Status: INCLUDED ON THE EXPERIMENTAL PUBLIC COMMUNITY ZSA TESTNET;
two observed confirmations.**
This owner-review report contains private wallet observations and must not be
used as public explorer evidence. The separate public receipt omits the
shielded amount and recipient. This is **NEW ZWA PROTOCOL WORK** using
**PRE-EXISTING community tooling**, not Phase 4E or mainnet functionality.

## Exact owner approval and pre-broadcast gate

The owner expressly approved a single public transfer submission naming
canonical node txid
`9c0cfc6d475f165ce0b491b5b793c869b38e5af5a50fc6c7ecde58d711cf0e77`.
The public root was the existing owner-only
`/private/tmp/zwa-pub-public-phase3a-20261012`; no protected Phase 4E wallet,
key, database, root or chain was accessed.

Immediately before submission, the root-bound identity check matched chain
`test`, NU7/ZSA branch `77190ad8`, `/Zebra:5.0.0/`, genesis
`03536e258babad397d59ce2995d9015d0791ac5272b8348e8f07f94143a72bb7`,
public block 1
`01747c32a6bd1bbfe5d91566d26c190719181908fa7560d9ef89da2204e82274`,
and the fixed public TLS endpoint `https://zsa.methyl.cc`. The observed tip
was **202132**, hash
`0131d90a2968b7d780c0a3c9ddce492968d7d1806ac8db71d5ac4895ec6770f9`.
The source issuance txid
`dfe0eed19c10cc0e6fad5f71227235ef5d08c232801a28e4a7ceafcbfc895340`
remained at index 1 in active block **202121**,
`0098a221eece2df7077ae6f0f7bfebc64fd76a64cab59b1357c55c0fc9276ec6`,
with **12 observed confirmations**. The issuer wallet rescanned exactly
**1,000,000 units** of AssetBase
`a1fd01da3b709214fc48f01e42e09867f23652f1b56721ff1460a0765d4b3f14`
and **980,000 Orchard zatoshis**, with zero Sapling or transparent ZEC.
The distinct recipient wallet still scanned zero ZSA and ZEC. Its exact
Orchard-only receiving address was rederived and matched the address in the
owner's approval package; it is omitted here to avoid recording a reusable
shielded address in the repository.

The signed candidate was **12,569 bytes**, SHA-256
`6646c667df6535184b31dc03534f21e73e6d8357e9caf6dc35f37772aab586a5`.
Its saved plan matched the approved canonical txid, exact AssetBase,
**250,000-unit** transfer, `recipient` role, public profile and expiry
**202167**. There was no submitted marker. The pinned Zebra parser
reserialized every byte exactly and computed the approved txid. The pinned
OrchardZSA verifier accepted the proof, spend and binding signatures. The
node's ZIP-317 code counted three conventional actions and **15,000
zatoshis** paid, equal to its conventional fee. A fresh public
`GetTreeState(202127)` and `GetBlock(202127)` check found an active Orchard
anchor equal to the signed transaction's anchor. A separate wallet audit
classified all three encrypted outputs, including the exact recipient
receiver, and found no unintended recipient or transparent change. The
wallet-library txid differs only because its reserialization clears
`enableZSA` at byte 2590 (`0x07` to `0x03`); no such normalization was
applied to the signed bytes or canonical node txid. The candidate was still
well before expiry at submission.

## Single submission and independent chain inclusion

The existing broadcast gate displayed the exact approved txid, **15,000**
zatoshi fee and **202167** expiry, then required its exact interactive
`BROADCAST <canonical txid>` phrase. It was submitted **once** through the
fixed public lightwalletd endpoint. Its reply was the same canonical txid
at submission tip **202134**. The client wrote one mode-0600 submitted
marker. No retry or second submission occurred.

An independent public RPC `GetTransaction` read returned the **same 12,569
bytes** and SHA-256 at height **202135**. A separate `GetBlock(202135)`
compact-block read found the canonical txid at **index 1** in active block
`028fa8bd065b64956fd4a98527ca253ad8bb5fdf8e08a85295fe6377457bb705`.
At the observed tip **202135**, that block had **one confirmation**. The
next independent read at tip **202136**, hash
`01011bcafc16c8008358b899901150bf38cb52ec2390e04d9d36892d3cffc608`,
found the same bytes and block membership with **two observed
confirmations**. The issuance also remained in its active block at that tip,
now with **16 confirmations**. The transfer was included on the same public
chain whose genesis, branch and block 1 were checked before broadcast. The
saved public receipt records the initial one-confirmation observation and
was not silently rewritten. Confirmations are observations, not finality;
this experimental chain can reorg or reset.

## Actual wallet accounting and receipt privacy

Independent post-inclusion scans showed the issuer holding **750,000 ZSA
units** of the exact AssetBase and **965,000 Orchard zatoshis**; the
recipient held **250,000 units** of that same AssetBase and **zero ZEC**.
Both reported zero Sapling and transparent ZEC. The asset inventory
reconciles **1,000,000 = 750,000 + 250,000** units, and the ZEC fee
reconciles **980,000 − 15,000 = 965,000** zatoshis. The client private
observation independently recorded the same issuer and recipient asset
balances. These amounts come from the wallets; a public observer cannot
read the shielded recipient or amount from the transaction alone.

The client saved one public and one private receipt, each mode 0600, in the
owner-only `receipts/` directory. The privacy-checked public receipt is
`/private/tmp/zwa-pub-public-phase3a-20261012/receipts/9c0cfc6d475f165ce0b491b5b793c869b38e5af5a50fc6c7ecde58d711cf0e77.1791856484.public.json`.
Its evidence class is `PUBLIC_CHAIN_INCLUSION`, its transfer amount is
`PRIVATE (shielded; not published)`, and its wallet observation is labelled
private. A separate check confirmed it contains no 250,000-unit literal,
shielded address, seed, viewing key, spending key or witness field. The
website public receipt list remains empty; nothing was deployed or published.
No private key, wallet database, witness or raw signed transaction was
committed or copied to a website response.

## Review gate

**PASS for one owner-approved transfer submission, observed inclusion and
both wallet scans.** The public lightwalletd and miner are operated by
others; the endpoint does not attest its exact deployed Zebra revision.
The operator-stated source pin remains
`zcash-shielded-assets/zebra@6a91831a6a9c18f667f59ab956c9437bf0bb5c5f`;
the wallet pin is `hhanh00/zkool2@51df4f0b356a886d5d2308811f215366ab648d65`.
This was not an atomic swap, ZIP-228 settlement, ZWA Groth16-gated
purchase or mainnet operation. No additional faucet claim, further
transfer, protected Phase 4E modification, deployment, push, merge, PR or
publication occurred. Stop for owner review before any new transaction.
