# Phase 3B public ZSA issuance approval package

**Historical pre-broadcast snapshot.** The owner later approved the exact
canonical txid below, and it was submitted once and included. The current
result is in [the Phase 3B issuance receipt](public-zsa-testnet-phase3b-issuance-receipt.md).
All “not broadcast” statements below describe the preparation checkpoint.

Observed 2026-10-13 01:17–01:19 UTC on `integration/public-zsa-testnet`.
**Status: PREPARED / NOT BROADCAST.** This is **NEW ZWA PROTOCOL WORK**
using **PRE-EXISTING community tooling**. The candidate creates an
experimental asset on the community ZSA Testnet. It is not ZWA's Phase 4E
atomic purchase or Zcash mainnet functionality.

## Funding checkpoint

The Phase 3A receipt was reviewed and committed locally as `5ba8629`
(`docs(public-zsa): record Phase 3A faucet funding receipt`). Read-only public
RPC still found faucet txid
`f531d5fa5ee97c21ea36edd044312fd91c32095a3aedb854f7878a8ab5f37113`
in active block **202078**,
`0298685122810cf643d7f725698a429116b38564f7062179d7dfd8710432571a`,
at index 1. At tip **202109**,
`026b17e91f3e6d7b2b565c37a92b4b41debb5d03bd97b063ccd8fbcfed3d932b`,
it had **32 observed confirmations**, with no detected reorganization of its
block. The issuer scan still showed 1,000,000 Orchard zatoshis, zero Sapling
and transparent ZEC, and no issued asset. The owner-only wallet database had
one unlocked Orchard note of 1,000,000 zatoshis at height 202078, zero spend
rows and zero pending wallet transactions. There were no prepared or submitted
transactions before this Phase 3B preparation.

Rootless preflight and root-bound identity matched chain `test`, branch
`77190ad8`, `/Zebra:5.0.0/`, genesis
`03536e258babad397d59ce2995d9015d0791ac5272b8348e8f07f94143a72bb7`,
public block 1
`01747c32a6bd1bbfe5d91566d26c190719181908fa7560d9ef89da2204e82274`,
and the fixed TLS endpoint `https://zsa.methyl.cc`. The issuer is the fresh
`issuer` role under `/private/tmp/zwa-pub-public-phase3a-20261012`. Its public
issuance validating key is
`009fa50c4cd8ea5b878377fb2494de0252194ef08891a4c82232af9e2ad6556aaa`.
No protected Phase 4E root or wallet was opened.

## Exact prepared transaction

| Field | Observation |
|---|---|
| Asset name | `ZWA-TESTNET-OWNER-1` |
| Asset description hash | `b26671184fc43e90d9523a02e5f78896d7c15fe5a67be3bf03164ee845e77cc8` |
| Canonical AssetBase | `a1fd01da3b709214fc48f01e42e09867f23652f1b56721ff1460a0765d4b3f14` |
| Issuance | **1,000,000 units**, finalized first issuance |
| Fee | **20,000 zatoshis**, four logical actions at 5,000 each |
| Expected issuer Orchard ZEC after inclusion | **980,000 zatoshis**, assuming this transaction is accepted and the scanned funding note remains unspent |
| Canonical node-derived txid | `dfe0eed19c10cc0e6fad5f71227235ef5d08c232801a28e4a7ceafcbfc895340` |
| Original signed bytes | 9,744 bytes; SHA-256 `17959a23595283ee0a99bbbd952b2dca6a0712636a35abd33baddd105bccfef0` |
| Build height and anchor | 202100; transaction Orchard anchor matched the current public `GetTreeState(202100)` and that state matched active `GetBlock(202100)` |
| Expiry | **height 202140**; at observed tip 202109, 31 blocks remained, and the client refuses submission once tip reaches 202137 |
| Current submission state | One private prepared plan; **no submitted marker, no broadcast** |

The exact signed bytes and plan are stored only under the owner-only public
root at `pending/<canonical txid>.tx` and `.plan.json`, both mode `0600` inside
a mode `0700` directory. The plan summary is also mode `0600`. The receiving
address, seed, spending key, viewing key, witnesses and raw signed bytes are
absent from this report and the website.

## Independent validation

- `zwa-node-check`, built from
  `zcash-shielded-assets/zebra@6a91831a6a9c18f667f59ab956c9437bf0bb5c5f`,
  accepted the complete V6/NU7 bytes and reproduced them **byte for byte**.
  Its own parser computed the canonical txid and the AssetBase above from the
  issuance validating key and description hash. It found one finalized issue
  action, 1,000,000 issued units and two Orchard actions with `enableZSA`.
- The pinned node Orchard library verified the **issuance authorization
  signature** against the transaction's NU7 sighash and first Orchard
  nullifier. It returned one valid newly issued asset record. This local
  check does not replace live consensus acceptance of proofs, anchors and
  current chain state.
- The wallet library's independent inspection found V6, branch `77190ad8`,
  four logical actions, a 20,000-zatoshi paid fee, no transparent input or
  output, no Sapling spend or output, no ZIP-233 amount, and the exact asset
  name hash and quantity. The pinned node fee function independently returned
  four conventional actions and **20,000 zatoshis**. [ZIP 317](https://zips.z.cash/zip-0317)
  specifies a 5,000-zatoshi marginal fee and two grace actions for its active
  revision. The [operator's source statement](https://forum.zcashcommunity.com/t/zsa-testnet/56884/16)
  pins the community fork used in the lab, but the live endpoint does not
  attest its deployed source or expose a read-only current fee-policy quote.
- Of the two public issuance notes, one is the required **zero-value reference
  note** with the protocol reference receiver. The **1,000,000-unit note**
  matches the fresh issuer wallet's Orchard receiver. An in-process viewing
  key check decrypted exactly one nonzero Orchard output: **980,000
  zatoshis of ZEC change to the issuer**. The transaction's 20,000-zatoshi
  fee and 980,000-zatoshi change reconcile with the single 1,000,000-zatoshi
  funding note. No nonzero issue note or ZEC change went to another receiver.
- The wallet-library txid is
  `3c67aabb01407d568323bc603c68730dc89e9122922e0ec0a7f792c22562e172`.
  Re-serializing the wallet's V6 view changes **only byte 1738** from `0x07`
  to `0x03`, clearing `enableZSA`; the original signed bytes retain `0x07`.
  The node's txid above is canonical. The client allows only this known
  representation difference and refuses any unexplained mismatch.
- The plan file and original bytes have the same SHA-256. The client `inspect`
  command re-read and rechecked them. The single-use asset-name marker points
  to this exact txid. No submission marker exists. Client tests confirmed
  default broadcast refusal, exact owner phrase, expiry margin and
  one-submission-per-txid behavior.

The issuer wallet balance remains 1,000,000 Orchard zatoshis because the
prepared transaction has **not** been submitted. No raw transaction or wallet
secret was written to the repository, frontend data, or command logs. The
website's public receipt list remains empty. Temporary read-only audit source
is outside the repository under `/private/tmp`; it prints counts and booleans,
not keys, addresses or witnesses.

## Historical proposed operation and separate approval gate

**Do not run this command without a new owner approval naming this exact
canonical txid.** After that approval, the owner-operated terminal command
would be:

```sh
ZWA_OWNER_BROADCAST_APPROVAL=dfe0eed19c10cc0e6fad5f71227235ef5d08c232801a28e4a7ceafcbfc895340 \
  /private/tmp/zwa-pub-build/tool-target/release/zwa-zsa-pub \
  --root /private/tmp/zwa-pub-public-phase3a-20261012 \
  broadcast --txid dfe0eed19c10cc0e6fad5f71227235ef5d08c232801a28e4a7ceafcbfc895340
```

The tool then requires an interactive owner to type exactly
`BROADCAST dfe0eed19c10cc0e6fad5f71227235ef5d08c232801a28e4a7ceafcbfc895340`.
Neither step had been taken at this preparation checkpoint. Recheck chain identity, funding, anchor continuity,
fee policy if newly exposed, and expiry before any later submission. If the
tip reaches **202137**, or the funding block/anchor reorgs, this candidate
must be replaced and its **new exact txid** must receive a new approval.

## Verification and remaining risk

`cargo test --locked --offline` passed: 14 client tests and one node-check
test. `git diff --check` passed. The only repository change in Phase 3B is
this review document; no code, dependency or website change was retained.
The Phase 3A funding document is the sole new local commit. No push, merge,
PR, deployment, further faucet claim, issuance broadcast, transfer broadcast,
or protected Phase 4E operation occurred.

**PASS for local issuance preparation; public acceptance remains untested.**
The community chain has one operator and may reorg or reset. The live endpoint
does not attest its build or fee policy. This transaction has a short expiry
window. The next action is a **separate owner approval for the exact canonical
txid above**, followed by a fresh pre-broadcast review; Phase 3A faucet
approval does not authorize it.
