# ZWA on the community ZSA Testnet (experimental)

**NEW ZWA PROTOCOL WORK**, built on **PRE-EXISTING community tooling**: Zkool's
`rlz` wallet library (MIT, Hanh Huynh Huu), the community Zebra fork
(`zcash-shielded-assets/zebra`, MIT/Apache-2.0) and the community lightwalletd
(`zcash-shielded-assets/lightwalletd`, MIT).

This directory issues an experimental ZSA and moves it between two separate
disposable wallets on the community ZSA Testnet. It is separate from ZWA's
Groth16-gated, human-approved atomic purchase on private regtest. It never
builds a swap and never splits one into two payments.

| Path | Purpose |
|---|---|
| `zwa-zsa-pub/` | Client: network binding, wallets, build-only issuance and transfer, independent inspection, owner broadcast gate, inclusion verification, privacy-safe receipts |
| `node-check/` | Parses prepared bytes with the node's own `zebra-chain@6a91831` and recomputes AssetBase with the node's Orchard fork |
| `lab/` | Private loopback node and lightwalletd running the same consensus code, plus the scripted lab acceptance test |

## Pins

| Item | Value |
|---|---|
| Public lightwalletd | `https://zsa.methyl.cc` (gRPC only; fixed in code) |
| Genesis | `03536e258babad397d59ce2995d9015d0791ac5272b8348e8f07f94143a72bb7` |
| Block 1 | `01747c32a6bd1bbfe5d91566d26c190719181908fa7560d9ef89da2204e82274` |
| Consensus branch | `0x77190ad8` (NU7 / librustzcash-fork `BranchId::Zsa`) |
| Node | `/Zebra:5.0.0/`, `zcash-shielded-assets/zebra@6a91831a6a9c18f667f59ab956c9437bf0bb5c5f` (confirmed by the operator on the Zcash forum, 2026-09-01) |
| Wallet library | `hhanh00/zkool2@51df4f0b356a886d5d2308811f215366ab648d65` (`rlz`, default features off, bundled Sapling parameters) |
| Wallet crate graph | Orchard `zcash-shielded-assets/orchard@60697dd`, librustzcash `zcash-shielded-assets/librustzcash@53813b3` |
| Node crate graph | Orchard `zcash-shielded-assets/orchard@ed66d7e`, librustzcash `hhanh00/librustzcash@09c0ea8` |
| Lab lightwalletd | `zcash-shielded-assets/lightwalletd@b657286` |

The wallet and node use **different** crate revisions. The lab acceptance test
is what shows they agree: the node's own consensus code validates
wallet-built issuance and transfers, and `node-check` reproduces the same
AssetBase.

## Safety properties

- Roots must be `/private/tmp/zwa-pub-lab-*` or `/private/tmp/zwa-pub-public-*`:
  owner-only, newly created, and bound at `init` to the live genesis and block 1.
  Protected Phase 4E roots fail the path check, and nothing there is ever opened.
- A lab root accepts only a loopback lightwalletd and refuses the public chain's
  block 1. A public root talks only to `https://zsa.methyl.cc`.
- Seeds are generated inside `rlz` from 32 bytes of OS randomness, stay in the
  wallet database, and are never printed. Wallets are never reused or overwritten.
- `prepare-*` commands build and sign, then save the bytes. They never send.
  The independent inspector checks for:
  - a V6 transaction on branch `0x77190ad8` that round-trips byte for byte;
  - a fee at least the node's ZIP-317 minimum and below a ceiling;
  - issuance: exactly one finalized action, with every note's AssetBase equal to
    `ZIP-227(ik, BLAKE2b(name))`;
  - transfer: an OrchardZSA bundle only, with no issuance, no transparent or
    Sapling outputs, and an output that the **recipient's own viewing key**
    decrypts to exactly the requested asset and amount.
- `broadcast` on a public root requires all of the following, and refuses by default:
  - `ZWA_OWNER_BROADCAST_APPROVAL=<exact txid>`;
  - an interactive terminal;
  - the owner typing `BROADCAST <txid>`.
  Each txid is submitted at most once per root, and only while more than three
  blocks remain before expiry.
- Public receipts are checked for private material by the client and again by
  the website. Addresses, viewing keys, balances and shielded transfer amounts
  never appear in them.

## Build (isolated target directories)

```sh
cd public-testnet/zwa-zsa-pub && CARGO_TARGET_DIR=/private/tmp/zwa-pub-build/tool-target cargo build --release
cd public-testnet/node-check && CARGO_TARGET_DIR=/private/tmp/zwa-pub-build/nodecheck-target cargo build --release
```

The lab also needs `zebrad` built from `zcash-shielded-assets/zebra@6a91831`
(`RUSTFLAGS='--cfg zcash_unstable="nu7"' cargo build --release --features internal-miner,tx_v6 --bin zebrad`)
and `lightwalletd` built from `zcash-shielded-assets/lightwalletd`. `lab/lab.sh`
reads them from `/private/tmp/zwa-pub-build/`.

The owner-operated public commands are in `docs/public-zsa-testnet-phase2-report.md`.
