# Phase 3C public shielded ZSA transfer approval package

**Historical pre-broadcast snapshot.** The owner later approved the exact
canonical txid below, and it was submitted once and included. The current
result is in [the Phase 3C execution report](public-zsa-testnet-phase3c-transfer-execution.md).
All “not broadcast” statements below describe the preparation checkpoint.

Observed 2026-10-13 01:40–01:47 UTC on `integration/public-zsa-testnet`.
**Status: PREPARED / NOT BROADCAST.** This is **NEW ZWA PROTOCOL WORK**
using **PRE-EXISTING community ZSA tooling**, not Phase 4E or mainnet work.

## Phase 3B closure and current source inventory

Both Phase 3B documents were checked against the saved public receipt and
original 9,744-byte signed issuance transaction. The asset, txid, inclusion
height and block hash, signed byte SHA-256 and 20,000-zatoshi fee matched. A
fresh public `GetTransaction` and compact-block read still found canonical
issuance txid
`dfe0eed19c10cc0e6fad5f71227235ef5d08c232801a28e4a7ceafcbfc895340`
in active block **202121**,
`0098a221eece2df7077ae6f0f7bfebc64fd76a64cab59b1357c55c0fc9276ec6`,
at index 1. At tip **202129**, hash
`00b8c7702843c6b43c25335dcc844992caac8312eb0bfd5c825a5db1cdce16ac`,
it had **nine observed confirmations**. The fresh issuer wallet scanned
**1,000,000 units** of exact AssetBase
`a1fd01da3b709214fc48f01e42e09867f23652f1b56721ff1460a0765d4b3f14`
and **980,000 Orchard zatoshis**. The recipient wallet still scanned zero
assets and zero ZEC. These are current wallet observations, not transfer results.

The Phase 3B documents were committed locally as `ec7b8ce`
(`docs(public-zsa): record Phase 3B issuance preparation and inclusion`).
The working tree was clean immediately after that commit. There was no push.

The owner-only public root is
`/private/tmp/zwa-pub-public-phase3a-20261012`. The issuer and recipient are
separate wallet databases, each created under this root for the public chain.
The root-bound identity check matched chain `test`, NU7/ZSA branch `77190ad8`,
`/Zebra:5.0.0/`, genesis
`03536e258babad397d59ce2995d9015d0791ac5272b8348e8f07f94143a72bb7`,
public block 1
`01747c32a6bd1bbfe5d91566d26c190719181908fa7560d9ef89da2204e82274`,
and fixed TLS endpoint `https://zsa.methyl.cc`. The live node does not
attest the operator-stated source commit
`zcash-shielded-assets/zebra@6a91831a6a9c18f667f59ab956c9437bf0bb5c5f`.
No protected Phase 4E path, key, database or chain was accessed.

## Exact signed candidate

- Kind: **Orchard ZSA shielded transfer**, V6, NU7 branch `77190ad8`.
- Asset: **250,000 units** of
  `a1fd01da3b709214fc48f01e42e09867f23652f1b56721ff1460a0765d4b3f14`,
  originally issued under off-chain name `ZWA-TESTNET-OWNER-1`.
- Sender and recipient: the fresh public-root `issuer` and distinct `recipient`
  wallets. The exact Orchard-only `uregtest` receiving address is presented
  directly to the owner with this approval package; it is omitted from the
  repository document to avoid publishing a reusable shielded address.
- Canonical **node-derived** txid:
  `9c0cfc6d475f165ce0b491b5b793c869b38e5af5a50fc6c7ecde58d711cf0e77`.
- Original signed bytes: **12,569 bytes**, SHA-256
  `6646c667df6535184b31dc03534f21e73e6d8357e9caf6dc35f37772aab586a5`.
- Built at public height **202127** using that height's Orchard tree anchor;
  expiry **202167**. This is a short-lived candidate. The client requires a
  safety margin before submission.
- Paid fee: **15,000 zatoshis**. The pinned community node's ZIP-317 code
  independently counted **three** conventional actions and returned a
  **15,000-zatoshi** conventional fee. Its own parsed Orchard ZEC value
  balance also gave 15,000 zatoshis paid. The endpoint offers no live fee
  quote, so a later broadcast would need a fresh policy check.
- The signed bytes and plan are stored mode 0600 under `pending/` inside the
  mode-0700 public root. The plan's byte hash matches the file. No submitted
  marker exists for this txid. There is no public transfer receipt.

## Independent validation

The pinned `zebra-chain` V6 parser accepted all bytes, reserialized them
**byte for byte**, computed the canonical txid above, found three Orchard
actions with `enableZSA`, no issue bundle, and expiry 202167. The wallet-side
reinspection independently found no transparent inputs or outputs, no Sapling
spends or outputs, no ZIP-233 amount, and no transparent change. The node's
OrchardZSA verifier validated the **Halo2 proof**, all **spend authorization
signatures**, and the **binding signature** against the node-computed NU7
sighash. These local checks cannot establish live consensus acceptance until
the network includes a future broadcast.

A separate audit used public `GetTreeState(202127)` and `GetBlock(202127)`;
their hashes matched and their Orchard root equaled the signed transaction's
anchor. It decoded the recipient address under this community network's
Regtest-style encoding and confirmed that it contains exactly one Orchard
receiver, distinct from the issuer address. Using only the two fresh wallets'
in-process viewing keys, it classified **all three encrypted Orchard
outputs**: **250,000 ZSA units** to the exact recipient receiver, **750,000
ZSA units** of change to the issuer, and **965,000 Orchard zatoshis** of ZEC
change to the issuer. It found no unclassified output or unintended recipient.
The fee reconciles **980,000 − 15,000 = 965,000 zatoshis**. No viewing key,
spending key, seed, witness or signed bytes were printed or written to the
repository or website.

The wallet library reports txid
`ae390e6fb76223dee96fda1716dc68b46f56250640e9f71a3ad57199cd81afd5`.
Its V6 reserialization changes **only byte 2590** from `0x07` to `0x03`,
clearing `enableZSA`; the original signed bytes retain `0x07`. This is the
same known representation issue observed for issuance. The node-derived txid
above is canonical for the unchanged bytes. Any additional mismatch must
fail closed.

## Expected state and owner gate

**No transfer has been submitted or included.** Current scanned balances
remain issuer **1,000,000 asset units / 980,000 Orchard zatoshis** and
recipient **0 asset units / 0 ZEC**. If this exact transaction is later
broadcast, accepted and scanned on the active chain, the expected asset
balances are issuer **750,000** and recipient **250,000** units. Expected
issuer Orchard ZEC is **965,000 zatoshis** and recipient ZEC remains zero.
These are predictions from the prepared bytes, not observed wallet balances.

**Stop for separate owner approval naming the exact canonical transfer txid.**
Before any later submission, recheck identity, issuance block continuity,
current chain height, anchor, expiry, funding and exact signed bytes. If the
candidate expires, its anchor is invalid, or its canonical txid changes,
prepare a new candidate and request fresh txid-specific approval. Do not
submit based on the wallet-library txid. No transfer, faucet claim, deployment,
mainnet operation, push, merge, PR or publication was performed in Phase 3C.

The network is experimental and run by others; a reorganization or reset can
invalidate this candidate. Public observers cannot see the recipient or the
250,000-unit amount in the shielded transfer; those details are established
here by wallet-side decryption of the signed candidate and would require
post-inclusion wallet scanning for a transfer receipt.
