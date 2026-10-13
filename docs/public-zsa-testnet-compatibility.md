# Public ZSA test network compatibility (Phase 1, read-only)

Observed 2026-10-12, 21:55–22:06 UTC. **NEW ZWA PROTOCOL WORK** (report only). No
code, configuration, wallet, key, or chain state was created or changed.

## Verdict

**No reachable public network can settle ZWA's current atomic ZSA-for-ZEC
transaction.** Two public networks are persistent and expose V6/ZSA issuance:
the community ZSA Testnet (`zsa.methyl.cc`) and the QEDIT `ZSA1_1` network
(`rpc.test-zsa.org`). Both run Zebra 5.x with NU7 (`0x77190ad8`) and reject the
transaction shape ZWA produces. The parsing failure happens **before** proof or
signature verification:

1. `"V6 transaction must contain exactly one action group"`. ZWA settlement
   uses three Orchard Action Groups.
2. `"nAGExpiryHeight must be zero for NU7"`. ZWA quotes bind
   `groupExpiry = targetHeight + 10`.
3. Their V6 serialization contains a `zip233_amount` field. Our pinned V6
   format lacks it, so the bytes do not align.
4. Neither network defines the `Swap` upgrade or consensus branch `0x88888888`.
   ZWA's quote digest and pinned tx tool require that branch.

The only endpoint that advertises `Swap` is `dev.zebra-swaps.zsa-test.net`.
It is an **empty** regtest node at height 0, with Zebra 2.0.0 and the
generic Zcash regtest genesis. Its deployed source commit cannot be determined.
It is not a meaningful independent public network (see below).

These are network blockers, not ZWA defects. ZWA's atomic swap must **not** be
replaced with two sequential payments. Public settlement of the swap stays
blocked until a public network runs multi-Action-Group swap consensus.

## Method and safety

- Only read-only calls were made: HTTPS `GET`; JSON-RPC `getinfo`,
  `getblockchaininfo`, `getnetworkinfo`, `getblockhash`, `getblock`,
  `getblockcount`, `getpeerinfo`, `getrawmempool`, `getmininginfo`; and
  lightwalletd gRPC `GetLightdInfo`, `GetLatestBlock`, and `GetBlock`.
- There were no `sendrawtransaction`, `submitblock`, `getblocktemplate`,
  `SendTransaction`, or faucet requests. No address was generated or disclosed.
- Source identity came from read-only GitHub API reads of
  `zcash-shielded-assets/zebra`, `zcash-shielded-assets/orchard`,
  `QED-it/zebra`, and `QED-it/zcash_tx_tool`. Third-party documents were read
  as data only.
- The protected Phase 4E chain (`/private/tmp/zwa4e-phase4e-b`) was not
  contacted.

## ZWA pinned baseline (local)

| Component | Pin |
|---|---|
| Zebra source | `QED-it/zebra` `0aef55cea41b83f17610e6ea708e59995f9e739f` (branch `zsa-swap-multiple-ag`), crate version 2.0.0, built with the `zsa-swap` feature |
| Zebra image | `qedit/zebra-zsa-swap-pinned` `sha256:540a3429ef14ff3c575258255e4310951606d16c86ec5e1c37e285ea95178185` |
| tx tool | `QED-it/zcash_tx_tool` lineage `217b979…` (`zsa-swap`) + local ZWA commits, HEAD `16052323904be8efbb9e0f92d059fa57c9198f8a` |
| Orchard / librustzcash | `QED-it/orchard` `d91aaf1…`; `QED-it/librustzcash` `5a55da9…` |
| Network | Regtest (`REGTEST_NETWORK`); NU7 = `0x77190ad8`, **Swap = `0x88888888`** active from height 1 |
| Tx format | V6, version group `0x77777777`, **multiple Action Groups**, non-zero `nAGExpiryHeight`, **no** `zip233_amount` |
| Quote binding | `network = "regtest"`, `consensusBranchId = 0x88888888`, `feePayer = DISPOSABLE_REGTEST_SPONSOR`, sponsor input 625,000,000 zat (regtest coinbase) |

The pinned tx tool fails closed on any other network. It checks
`BranchId::for_height(&REGTEST_NETWORK, …) == BranchId::Swap` and rejects
otherwise (`independent_purchase.rs`, `transactions.rs`).

## Endpoint findings

### 1. Community ZSA Testnet — `zsa.methyl.cc` (+ faucet)

| Item | Observed |
|---|---|
| Responds | Yes. **gRPC only** (nginx; ECC lightwalletd `v0.0.0.0-dev`). No public JSON-RPC: `POST` JSON-RPC returns 415 `application/grpc`. |
| Backend | `/Zebra:5.0.0/`, lightwalletd `chainName = "test"` |
| Genesis | `03536e258babad397d59ce2995d9015d0791ac5272b8348e8f07f94143a72bb7`, taken from block 1's `prevHash`. This **exactly matches** `ZSA_TESTNET_GENESIS_HASH` in `zcash-shielded-assets/zebra` `merge` @ `6a91831a6a9c18f667f59ab956c9437bf0bb5c5f` ("add ZSA testnet with all NUs at height 1") |
| Network magic | `b"ZSA1"` (source), port 18233, no DNS seeders |
| Upgrades / branch | All NUs through NU7 at height 1; tip branch `77190ad8`; **no Swap upgrade** |
| Tip | 201,946 (faucet `/api/info` agrees; faucet `healthy: true`) |
| Address encoding | Faucet and community wallets use `uregtest1…`, `zregtestsapling1…`, and `tm…`. This is regtest HRP despite Zebra network kind "Testnet". |
| Faucet | 0.01 tZEC per claim, one per address per 5 min, daily cap 10, Cloudflare Turnstile |
| Block production | Operator internal miner (Equihash, minimal difficulty), per source config |
| Stack | `zcash-shielded-assets/orchard` `ed66d7e…` (a fork of `hhanh00/orchard`) and `hhanh00/librustzcash` `09c0ea8…`. Compared with our Orchard pin, it is **diverged: 98 ahead / 203 behind**, with changes in `circuit.rs`, circuit descriptions, `issuance/auth.rs`, `sighash_kind.rs`, `keys.rs`, `note/asset_base.rs` |
| V6 rules (source) | Exactly one Action Group; `nAGExpiryHeight == 0`; includes `zip233_amount`; features `internal-miner,tx_v6` (no `zsa-swap`) |
| Explorer / indexer | None found. lightwalletd compact blocks only |
| Deployed commit | Not reported by the node. It is inferred from the exact genesis match to `merge@6a91831`, plus a third-party wallet note saying so. Later commits on `merge` cannot be excluded |

### 2. QEDIT ZSA test environment — `rpc.test-zsa.org`

| Item | Observed |
|---|---|
| Responds | Yes. Public JSON-RPC over HTTPS (Cloudflare), no auth. `GET` returns 405 |
| Version | `/Zebra:5.2.0/`, protocol 170160 |
| Genesis | `f41f61ddf9610ae40f519ad2e9e543e065ef0357eeecf5a4cc08a765d9505756`. This **exactly matches** `testnet-config.toml` on `QED-it/zebra` `testnet-zebra-v5.2.0-ZSA.1` @ `e2f0f8b9203cafd4343540046a1a312f213a5108` (also branch `zsa1` @ `d955a2d…` + 20 deploy commits) |
| Network | Custom testnet `network_name = "ZSA1_1"`, magic `[0,1,0,255]`, **`disable_pow = true`**, no peers (`connections: 0`), cookie auth disabled |
| Upgrades / branch | NU5, NU6, NU6.1, NU6.2, NU7 at height 1; tip/next branch `77190ad8`; **no Swap upgrade** |
| Tip | 4,930, `c69559c7b226c61f1b67e65c2eb784d3db2df754f0df0aa834f45f8f01f45b35`, block time 20:55:45 UTC. The node warned "chain updates have stalled … 64 minutes" |
| Activity | Blocks 1 and 4,930 each hold a V6 coinbase plus a V6 **ZSA issuance** tx (`issuanceexists: true`, 2 Orchard actions, `zip233amount: 0`, value balance 0, `total_fees: "0"`, tx expiry = height + 40). This is consistent with the QEDIT tx-tool CI job that targets `rpc.test-zsa.org` every 15 minutes |
| Value pools | Transparent 7,596.821875 tZEC; Orchard 0. Coinbase pays the operator address `tmLTZegcJN5zaufWQBARHkvqC62mTumm3jR`. **No public tZEC faucet** was found for this network |
| V6 rules (source) | Exactly one Action Group; `nAGExpiryHeight == 0`; includes `zip233_amount` |
| Explorer / indexer | **Yes.** ZipherScan at `https://cipherscan.test-zsa.org` (`/api/blocks`, `/api/info`, and `/api/blockchain-info` answer and agree with RPC) |
| Persistence | The current deploy notes describe a persistent state volume. The base `testnet-config.toml` says `ephemeral = true`, and the history mentions a removed ECS node. Treat a reset as possible |

### 3. Swaps endpoint — `dev.zebra-swaps.zsa-test.net`

| Item | Observed |
|---|---|
| Responds | Yes. JSON-RPC (`getnetworkinfo` not implemented) |
| Version | `/Zebra:2.0.0/`, the same crate version as our pin. **Exact commit unknown.** The QEDIT swaps-node deploy branches (e.g. `arseni-add-logs-to-swaps-node` `38a038c…`) are diverged from our pin (26 ahead / 241 behind) |
| Genesis | `029f11d80ef9765602235e1bc9727e3eb6ba20839319f761fee920d63401e327`. This is the **standard Zcash regtest genesis**. Every regtest chain, including our private ones, shares it, so it identifies nothing |
| Upgrades | NU5 … NU7 and **Swap `0x88888888`**, all `pending` at height 1 |
| State | **Height 0**, empty mempool, no peers, no blocks ever mined |
| Explorer | None |

Using this node would mean mining every block ourselves on a third party's
empty, resettable regtest. Its only "independence" is that a third-party binary
would run validation. Unless the deployed commit is shown to match our pin, it
gives no stronger guarantee than our local regtest. Mining on it also writes to
infrastructure we do not operate, so owner consent is required first.

### 4. Faucet — `faucet.zsa.methyl.cc`

Covered under §1. It serves the community network only.

## Compatibility matrix

| Requirement | Community ZSA Testnet | QEDIT `ZSA1_1` | Swaps dev node |
|---|---|---|---|
| Independently operated, persistent | Yes, with real (easy) PoW miner | Single operator node; **no PoW**, so blocks can be made by any RPC caller | Empty regtest |
| Public read access | gRPC lightwalletd only | JSON-RPC + explorer | JSON-RPC |
| V6 tx accepted at all from our pinned tool | **No**: `zip233_amount` layout, branch ID | **No**: `zip233_amount` layout, branch ID | Unknown; plausible only if commit matches pin |
| ZSA issuance | Supported by network; requires a different tool stack | Supported (observed on chain); requires QEDIT `main`-lineage tool | Unknown, never exercised |
| Single-group ZSA transfer | Supported by network design; not observed by us | Supported by network design; not observed by us | Unknown |
| Multi-Action-Group swap | **Blocked** (parse rule) | **Blocked** (parse rule) | Advertised (`Swap` pending) |
| tZEC for a ZEC leg / fees | Faucet 0.01 tZEC per 5 min | **None found** | Self-mined only |
| Fee rule | Consensus allows zero fee (no ZIP-317 consensus floor in Zebra); mempool relay min applies | Zero-fee V6 blocks observed | Zero-fee blocks accepted in our local pinned equivalent |
| Expiry rule | Tx expiry normal; group expiry must be 0 | Tx expiry normal (observed height + 40); group expiry must be 0 | Non-zero group expiry allowed (if pin-equivalent) |
| ZWA AssetBase derivation | Same ZIP-227 structure (`0x00 ‖ ik ‖ asset_desc_hash`, Blake2b). Byte equality of `ik` encoding **not tested** | Not examined | Same if pin-equivalent |
| Explorer | None | ZipherScan | None |

## Required ZWA revalidation before any port (Phase 2 inputs)

These were identified in the source, and none was changed:

- `checkout/quote.py`: `network == "regtest"`, `KNOWN_BRANCH = 0x88888888`,
  `feePayer == "DISPOSABLE_REGTEST_SPONSOR"`, sponsor input fixed at
  625,000,000 zat, and `groupExpiry = target + 10`. Each rule is regtest- or
  Swap-specific. A public-network quote must be a **new quote version**,
  because changing v1 would weaken the existing quote guarantees.
- The pinned tx tool assumes `REGTEST_NETWORK`, mines its own blocks with
  `getblocktemplate`/`submitblock`, and spends height-2 coinbase. None of that
  is available on the community network, and mining would be inappropriate on
  QEDIT's shared node.
- The Groth16 buyer-eligibility authorization is off-chain and network-agnostic.
  It can be preserved unchanged, but any network-bound public inputs
  (quote digest, receiver commitments) must be recomputed under the new quote
  version.
- Recipient addresses: the community network uses `uregtest1` HRP, and QEDIT
  `ZSA1_1` encoding was not checked. All disposable identities must be new. No
  protected-regtest seed or wallet may be reused.

## Precise network blocker for the swap (for owner record)

> As of 2026-10-12, the two persistent public ZSA networks (community ZSA
> Testnet, genesis `03536e25…2bb7`, Zebra 5.0.0; QEDIT `ZSA1_1`, genesis
> `f41f61dd…5756`, Zebra 5.2.0) implement NU7 V6 transactions restricted to
> exactly one Orchard Action Group with `nAGExpiryHeight = 0`. They define no
> Swap network upgrade (`0x88888888`). A ZWA settlement transaction (three
> Action Groups, non-zero group expiry, Swap branch) is rejected during
> deserialization on both networks. The only public node advertising Swap
> (`dev.zebra-swaps.zsa-test.net`) has mined no blocks and runs an unidentified
> commit.

## Options for owner decision

1. **Community ZSA Testnet: issuance + transfer only (recommended for Phase 3).**
   It has a real independent miner and a faucet. It needs a new isolated tool
   built on the community stack (`zcash-shielded-assets/orchard`,
   `hhanh00/librustzcash`) and a lightwalletd submission path. The pinned tx
   tool cannot be used. Phase 5 verification would use lightwalletd
   `GetBlock`/`GetTransaction` through a read-only server proxy, since no
   explorer exists. Swap stays blocked.
2. **QEDIT `ZSA1_1`: issuance + transfer only.** It offers JSON-RPC plus the
   ZipherScan explorer, which makes the Phase 5 page easiest. However, there is
   no PoW and a single operator, so inclusion shows that QEDIT's node validated
   the tx, not that an independent miner ordered it. No tZEC source was found,
   which blocks any ZEC leg and positive fee. It needs the QEDIT `main`-lineage
   tool.
3. **Swaps dev node: swap only, with owner consent.** Before any submission,
   the deployed commit must first be shown to equal our pin. Every block would
   be mined by us. Independence is weak, and state is likely to reset.
4. **Wait** for a public network that adopts multi-Action-Group swap consensus.
   Keep public verification claims limited to local regtest evidence until then.

This paragraph records the **Phase 1 state when observed**. Phase 2 isolated
laboratory validation was subsequently completed; see
`docs/public-zsa-testnet-phase2-report.md`. No public transaction has been
broadcast. Public faucet use and each public broadcast require separate
explicit owner approval.

## Third-party attribution

Network software and sources belong to their authors: Zcash Foundation (Zebra),
QED-it (Zebra ZSA fork, `zcash_tx_tool`, ZipherScan deployment), and the
`zcash-shielded-assets` / `hhanh00` maintainers (community ZSA Testnet, Orchard
and librustzcash forks). The ZWA pins remain as recorded in
`docs/decisions/0003-experimental-zsa-stack.md` and `exchange/config.py`.
