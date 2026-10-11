# Phase 3.5F: independent wallet signing for one ZSA purchase

## Verdict and boundary

**INDEPENDENT_WALLET_PURCHASE_ACCEPTED** on one disposable, loopback-only, pinned experimental Zebra regtest. A buyer wallet process and a seller wallet process, each holding only its own seed in its own sandboxed home, separately reviewed the same exact terms, each built, proved and signed only its own ZIP 228 Action Group, and handed the coordinator that signed group plus its binding-key share. The coordinator, which never had either party's seed or spending key, verified and assembled the groups with a disclosed disposable fee sponsor and submitted **one** transaction. Zebra accepted it at height 103. Afterwards, each wallet scanned the accepted chain with its own keys and observed exactly its expected change.

This is an experimental regtest demonstration only. It is not production mainnet functionality, not a Zodl integration, not a frontend, and not a checkout API. Zcash consensus does not enforce ZWA compliance. The settlement says nothing about whether the custom asset is a legally authenticated real-world asset. The most important limitation: **pinned Zebra does not enforce Action Group expiry**. A released party authorization therefore stays executable until its input notes are spent. Only one-time, interactive, single-block approvals were used, and standing seller orders must not be advertised (see "Expiry security gate"). Stop for owner review.

| Separate status | Result |
| --- | --- |
| `PURCHASE_SETTLEMENT_STATUS` | `ACCEPTED_IN_BEST_CHAIN` at height 103 on this disposable regtest |
| `FINALITY_STATUS` | `PENDING_CONFIRMATIONS_1_OF_3`, then `CONFIRMATION_DEPTH_REACHED_3_OF_3_NOT_CONSENSUS_FINALITY` after two empty blocks |
| `ROOTED_LINEAGE_STATUS` | `UNSUPPORTED_RECEIVED_SWAP_NOTE_NOT_V2_REACHABLE` |
| `ELIGIBILITY_STATUS` | `NOT_EVALUATED` |

## Starting state, pins and freeze rules

`AGENTS.md` was read first. The product checkout began clean on `integration/zwa-checkout-poc` at `2a1953490efc69932d573160b8a4ae957891fe82` (the accepted Phase 3.5E record); only the untracked editor folder `.cursor/` was present and it was left alone. The non-frozen isolated tx-tool worktree `/private/tmp/zwa35c-tx-tool` began clean on `phase35e/zsa-for-zec-purchase` at `eda8a88f7cd8ca76e3892c6eff2b39389dd0e8d7`. This work used a new local branch, `phase35f/independent-wallet-signing`, created from that commit; the `phase35e` branch still points at `eda8a88`.

The original tx-tool checkout at `217b979ee01afb844190a162fb77874135aef587` still has its pre-existing edits to `src/commands/test_asset_swaps.rs` and `src/components/transactions.rs`, and its quarantined `zcutil/fetch-params.sh` is still deleted. None of these were touched, restored, or executed.

These checkouts were clean before and after the work:

- frozen Zebra source at `0aef55cea41b83f17610e6ea708e59995f9e739f`;
- Orchard discovery at `d91aaf146364a06de1653e64f93b56fac5b3ca0f`;
- librustzcash discovery at `5a55da948498dd0995d0f438b4c8e9a3f0150154`;
- Zebra discovery at `0aef55cea41b83f17610e6ea708e59995f9e739f`;
- frozen research at `3e7115d0e4c2f1cf36355f028447fc1b58290583`.

The node image was `sha256:540a3429ef14ff3c575258255e4310951606d16c86ec5e1c37e285ea95178185`, run as container `zwa-phase35f-regtest` and bound only to `127.0.0.1:18243`. Its built-in regtest configuration uses ephemeral state. Older phase containers on other ports were not touched.

The tx-tool `Cargo.toml` and `Cargo.lock` did not change, so the Orchard, librustzcash, halo2 and sapling pins are unchanged.

## Wallet implementation investigated

Status vocabulary: **REUSED** (pinned code used as-is), **NOT REUSABLE** (incompatible or unpinned), **ABSENT**.

| Capability | Pinned swap tx-tool (`217b979` lineage, Orchard `d91aaf1`, librustzcash `5a55da9`) | Experimental ZSA wallet CLI (`origin/claude/zsa-wallet-tool-dev-8e6j5m`, tip `19217f3`) |
| --- | --- | --- |
| Separate wallet creation and key storage | ABSENT: one in-memory seed per `User`; SQLite note store chosen by `DATABASE_URL` | Per-config seed phrase and `db_path`; NOT REUSABLE (different pins, below) |
| ZEC and ZSA balance tracking | REUSED: `User::balance` over scanned notes | Present (`balance`, `assets`, asset registry) |
| Note selection | REUSED: `select_spendable_notes` | Present for transfers only |
| Shielded receiving addresses | REUSED: ZIP 32 account 0 Orchard address | Present (unified-address display) |
| Viewing-key scans | REUSED: IVK trial decryption and OVK recovery while syncing | Present, plus a persisted tree state |
| Transaction-effect review | ABSENT; added here (own-effect accounting from the signed group) | ABSENT |
| Spend authorization | REUSED: `apply_signatures_for_action_group` over the group commitment | Ordinary transactions only; no Action Groups |
| Locally isolated signers | ABSENT; added here (one process per wallet) | ABSENT |

The experimental wallet CLI forks from the tx-tool `main` lineage (merge base `df86738`), not from the `zsa-swap` lineage that this stack uses. It pins Orchard `77d3274cb1f4620e9a1b86477c490fa123dff6bd` and librustzcash `0ea737548f7aea6124056df54d55f6c5a35ef914`. It has no swap, Action Group or `SwapBundle` code. The branch tip is an AI-authored remote branch dated 2026-07-30 and has not been merged into QED-it `main`. Its wallet ideas (per-config database selection, persistent tree state, asset registry) are useful design references, but **no code was imported**: its pins are incompatible with the accepted swap stack, and it is unpinned with respect to this project.

ZIP 374 (PCZT) was inspected as a signing-format reference. **PCZT is not compatible with the pinned ZSA Action Group format.** The pinned `orchard::pczt` action has no asset, split-note, burn, Action Group or expiry fields, and the pinned `pczt` crate has no Action Group roles. PCZT support is not claimed. The format used instead is the pinned v6 `write_orchard_swap_bundle` / `read_orchard_swap_bundle` encoding of exactly one signed Action Group.

## Architecture and key custody boundaries

There are three roles, each a separate OS process invocation of the same pinned tx-tool binary. Each role has its own private home directory (mode `0700`), its own seed file (mode `0600`, created with `create_new` from `OsRng`) and its own SQLite wallet database. Every invocation runs under `env -i` with a macOS `sandbox-exec` profile that denies reads and writes of the other roles' homes. A wallet process refuses to start unless `DATABASE_URL` resolves inside its own private home. The harness verified the boundary before any signing: under each profile, reading another role's seed failed with `Operation not permitted`. The five pairs checked were coordinator→buyer, coordinator→seller, buyer→seller, seller→buyer and buyer→sponsor.

| Role | Holds | Never holds |
| --- | --- | --- |
| Buyer wallet (`wallet-signer --role buyer`) | Buyer seed, its Orchard spending key, IVK/OVK, note store | Seller or sponsor keys; a random, unpersisted, unused miner seed fills the `User` field |
| Seller wallet (`wallet-signer --role seller`) | Seller seed, its Orchard spending key and ZSA issuance key, note store | Buyer or sponsor keys |
| Coordinator (`purchase-coordinator`) | Disposable regtest sponsor only: the public default regtest miner key that funds the fee and a fresh Orchard seed for the sponsor's fee change | Any buyer or seller seed, spending key or viewing key |

What crosses a process boundary, as files in the exchange directory:

- public raw Orchard addresses;
- the seller's signed issuance transaction;
- the purchase terms JSON;
- each party's signed-group envelope;
- the assembled transaction and the receipt.

An envelope contains the one-group encoding, the group's native-ZEC balance, the group commitment, and the group's **binding-key share** `bsk`. This share is ZIP 228's matcher input: the pinned `SwapBundle::new` sums the shares to sign the aggregate binding signature. It authorizes no spend and cannot alter a signed group. However, together with the group it is a usable bearer authorization until the group's notes are spent, and it is treated that way below.

The leak audit ran inside each wallet process, so the secrets never left it. It searched every file in the exchange directory, all logs, the coordinator home, and the committed tx-tool `src/` and `scripts/` trees for that wallet's seed, account spending key and full viewing key. Raw bytes, lowercase hex and uppercase hex were all checked. It found **0 hits** for every secret: buyer over 56 files, seller over 57. No HTTP layer exists. No seed, key, wallet database or exchange file is committed to either repository.

## Signing protocol

1. The seller's process issues 100 units of a custom ZSA to its own address with its own issuance key, and hands over the signed issuance transaction. Each wallet reports its public receiving address.
2. The coordinator writes exact `PurchaseTerms` (version, network, consensus branch, asset, quantity, ZEC price, matcher fee 0, network fee, both recipients, sponsor outpoint/input/change, target height, transaction and group expiry, 32-byte random nonce). It announces their BLAKE2b-256 hash, personalized with `ZWA35F_TermsHash`.
3. **Review.** Each wallet scans the chain with its own keys, recomputes the hash and repeats every context-free check with checked arithmetic. It requires its own address in its own recipient field, sufficient own balance, and the next block height equal to the terms' target height. It then prints exactly what it would spend and receive.
4. **Authorize.** Signing requires the approved hash on the command line. The wallet then:
   - consults its journal (one live authorization at a time, never a reused nonce);
   - builds its own Action Group, spending its own notes and paying its own outputs only (buyer: −10,000 native, +7 units; seller: −7 units, +10,000 native);
   - creates the Halo2 proof and SpendAuth-signs the group commitment;
   - verifies the proof and signatures;
   - checks group expiry, the empty burn set, the native balance, and the anchor against its own scanned tree;
   - recomputes its **own effects from the signed group itself** (own nullifiers minus own OVK-recovered outputs) and requires exact equality with the terms.

   Only then does it write the envelope and append the journal entry.
5. **Assemble.** The coordinator re-checks the terms and the signing window, opens both envelopes, and checks role, terms hash, commitment, group expiry, burns and native balance. It verifies both proofs and all SpendAuth signatures, checks that the groups share no nullifier and use its own chain anchor, and adds the sponsor input and the sponsor's shielded fee change.

   It then re-verifies the final transaction, all of which must hold:

   - the transaction round-trips to the same txid;
   - the branch is SWAP and the transaction expiry is 143;
   - there are exactly 3 groups, and the first two carry the authorized commitments;
   - every proof and SpendAuth signature verifies, and the aggregate binding signature verifies;
   - the aggregate value balance is −624,995,000;
   - the sponsor group has exactly one OVK-recovered output, the sponsor change;
   - there is exactly one transparent input (the terms' outpoint) and zero transparent outputs;
   - the fee, recomputed from the prevout, is 5,000.
6. **Submit and observe.** The coordinator re-verifies the transaction and the signing window, then submits one fee-paying block. A separate `curl` client queries the node. Each wallet process then rescans with its own keys.

The approval step was scripted: the harness passed the hash the coordinator announced. Each wallet nevertheless recomputed that hash from the terms file it read, and enforced its own recipient, balance, window and effect checks. No human reviewed the terms in this run.

## Authorization effect coverage

These are exactly what each signature covers in the pinned Orchard, librustzcash and Zebra code. Zebra's swap path checks each group's proof and its SpendAuth signatures against **that group's own commitment** (`action_group_sighashes`). It checks the binding signature against the **swap-bundle commitment** (`swap_bundle_sighash`), not the full-transaction sighash.

| Signature | Signer | Covers | Does not cover |
| --- | --- | --- | --- |
| Buyer group SpendAuth signatures | Buyer wallet | Every buyer action's nullifier, note commitment, ephemeral key, ciphertexts, value commitment and `rk`; the group flags, anchor, group expiry and burns (`hash_action_group`) | The group's value balance as a number, other groups, network or branch ID, fees, transparent parts, transaction expiry |
| Seller group SpendAuth signatures | Seller wallet | The same fields for the seller's own group | The same exclusions |
| Halo2 proof per group | The party that built the group | Note ownership, value commitments, nullifier derivation and the anchor for that group | Anything outside the group |
| Aggregate binding signature | Coordinator, from the sum of the shares | All groups in order plus the aggregate value balance; per-asset balance across all groups | Transparent parts, fee semantics, transaction expiry, branch ID |
| Sponsor transparent signature (SIGHASH_ALL) | Disposable sponsor (coordinator) | The whole transaction digest, including the Orchard swap digest, branch ID, transaction expiry and transparent fields | Nothing further; it binds only the sponsor, not the parties |

Because each party signs only its own group, **each party's safety is self-contained**:

- The buyer's signature guarantees that the buyer spends only the notes it chose, and that if the transaction is valid it receives exactly 7 units of this asset to its own address.
- The seller's signature guarantees that it delivers 7 units net and receives exactly 10,000 zatoshis to its own address.

**Counterparty identity is not cryptographically bound by either party's signature.** The coordinator cannot redirect, resize or alter a signed group, but it may pair it with any other group that balances it. The network fee, the sponsor's inputs and change, extra sponsor inputs and the transaction expiry are bound only by the sponsor's signature and the coordinator's own policy checks; the Zebra probes below demonstrate this. Network binding is implicit through the group anchor, which commits to this chain's note-commitment tree; the branch ID is not in the group commitment. An arbitrary JSON quote signature would not add consensus coverage, and none is relied on: the terms hash is a local approval token, and all consensus-level binding comes from the signatures above.

## Expiry security gate

The gate was measured on the pinned node, not assumed. After the purchase, the sponsor built a zero-fee self-transfer group from its own change note and submitted three candidates through Zebra's `getblocktemplate` `mode: "proposal"`. This mode runs block acceptance rules except proof-of-work and never commits the block. The candidates were proposed for block 104:

| Candidate | Group expiry | Transaction expiry | Zebra |
| --- | ---: | ---: | --- |
| Control | 114 | 144 | `VALID` |
| Expired group | **1** | 144 | **`VALID`** |
| Expired transaction | 114 | 103 | `REJECTED`: `ExpiredTransaction { expiry_height: 103, block_height: 104 }` |

So pinned Zebra **enforces the global transaction expiry but not Action Group expiry**. This matches the source: Zebra parses the group's `expiry_height` and checks nothing against it. Group expiry is signed, but it is not a deadline. Transaction expiry is a deadline, but it is chosen by the coordinator and signed only by the sponsor, not by either party. The only consensus-backed bound on a party's released authorization is its spent-note nullifiers. Replay of the accepted groups was rejected with `DuplicateOrchardNullifier` (see the adversarial outcomes).

Result: **safe consensus-backed expiry for party authorizations cannot be established on this pin. Reusable standing seller orders must not be advertised.** This experiment used one-time, interactive approvals bound to a single target height. Each signer signs only when the next block is the target height; the coordinator assembles and submits only in that same block window. The wallet journal refuses a second authorization while an earlier one's input notes are unspent, and never reuses a nonce. These are **wallet-local and coordinator-local checks, not consensus deadlines**. The practical cancellation path is for the wallet to spend its own input notes. That is not implemented here.

## Accepted transaction evidence

| Item | Value |
| --- | --- |
| Terms hash | `6cd1e0921ca2a07304d6b51bbfbbc935cd926027aa77827e144b402f88186739` (nonce `cc0883e314fef49e53086f4eeb44fb68b9a95ee071f6e1a3ec9109d9ad532699`) |
| Custom asset base | `a2f7897b34d296f005e728bb660a4f3c19a0dc2e7611d17cddcef5853d000c86` (issued by the seller wallet, block 102, txid `19f758a640a50f69850b0107d12f537e85744dbcb6340770c18d337ffd999713`) |
| Buyer funding | 625,000,000 shielded zatoshis from the height-1 coinbase, block 101, txid `1793633e150b0947fa5e3d02f846459b5cd2b5414115ec26fb08b25c19c0383c` |
| Buyer group commitment | `be79e77fae0c7146230690e9f8e98fc25c1e33f9db4b1006f7c2257482a26f50` (2 actions, native balance +10,000, expiry 113) |
| Seller group commitment | `16cef193409a2b9267ae9aa5d2255ff7597e31d0d99a40befd74439032472bef` (2 actions, native balance −10,000, expiry 113) |
| Fee sponsor input | height-2 coinbase `52d16b3f37b8e0a2cc1cf889b5ac7f8be6de18aef724fd601afbef1d9843cf6e:0`, 625,000,000 zatoshis |
| Purchase txid | **`99d9982716cbbeb1937691fef76511f0485e527c7cce4961e4510c1b9f21fb25`** |
| Transaction bytes | 28,060; SHA-256 `c3d50deab46c583b072459cf6245fdb72a036e29183a16c0492ae404178054ff` |
| Shape | 3 Action Groups, 1 transparent input, 0 transparent outputs, aggregate value balance −624,995,000, transaction expiry 143 |
| Height / RPC block hash | **103** / **`1a7e7d08f4d20be37c05b6fa69f84a4eea5bc487ce8e9dd5438eba2de3da56da`** |
| Block transactions | coinbase `c4ab3733b7e0a766ad40d49b7ac8497c75f63cad3342073b01357cbc9daecb81`, then the purchase |

The Zebra log records `submit block accepted` for that hash at height 103 and only empty blocks after it (104 `1ed52214…fdb7`, 105 `7d80fd67…8116`). Every adversarial candidate appears only as a non-committing `CheckProposal` result. A separate `curl` client then queried the node:

- `getblockcount` returned 103;
- `getblock 103` returned the hash above and contained the purchase txid, with 1 confirmation;
- `getrawtransaction` returned height 103, 1 confirmation and 28,060 bytes **byte-identical** to the assembled transaction.

The miner fee is not as freshly evidenced. The node's state was ephemeral and the container was stopped after the run, so the accepted coinbase's raw bytes were **not** independently decoded this time. The 5,000-zatoshi miner fee is supported by:

- the tool's pre-submission check (`coinbase_outputs_zat=[625005000]`);
- Zebra's acceptance of the block (Phase 3.5B showed this node rejects a mismatched miner fee);
- the coinbase txid being identical to the Phase 3.5E coinbase that was independently decoded as one 625,005,000-zatoshi output.

The retained local evidence is in `/private/tmp/zwa35f/`. It includes the terms, envelopes, assembled transaction, receipt, independent RPC JSON, per-role logs and the Zebra log. Its `buyer/`, `seller/` and `coordinator/` homes hold disposable regtest seeds and must not be copied into a repository. The harness transcripts are `/private/tmp/zwa35f-harness.log` and `/private/tmp/zwa35f-harness-resume.log`.

**Run integrity notes.** The full harness run submitted the purchase, which was accepted, and wrote the receipt. Its next step then failed while parsing Zebra's verbose `getrawtransaction` output, which has no `txid` field. Because the harness output was piped to `tee`, that failure did not stop the shell with a non-zero status. The parser was fixed to compare returned bytes instead, and the post-acceptance steps were rerun with `MODE=resume-after-acceptance` against the same chain. That mode never builds or submits a purchase.

The run's start line shows `tool_commit=eda8a88` because the code was not yet committed. The Rust sources were not edited between the pre-run release build and commit `778421c`; only the harness script changed afterward. A later rebuild recompiled the crate only because `build.rs` embeds `git rev-parse HEAD`.

## Wallet balance observations

Each line below comes from that wallet's own process scanning the accepted chain with its own keys. The sponsor line comes from the coordinator.

| Wallet | Before purchase (height 102) | After purchase (height 103) | Change |
| --- | --- | --- | --- |
| Buyer | 625,000,000 native zat, 0 asset units | 624,990,000 native zat, **7** asset units | −10,000 zat, +7 units |
| Seller | 0 native zat, 100 asset units | **10,000** native zat, 93 asset units | +10,000 zat, −7 units |
| Disposable sponsor | (transparent coinbase only) | 624,995,000 shielded native zat | 625,000,000 input − 5,000 fee |

Fee funding is accounted separately from the buyer's payment. The sponsor funded the 5,000-zatoshi network fee from its own coinbase, and its change went to the sponsor, not to the buyer as in Phase 3.5E. The buyer's only native-ZEC movement is the 10,000-zatoshi price.

## Adversarial test outcomes

Layers: **signer** = the wallet process refused before signing; **coordinator** = the coordinator refused before assembly or submission; **status** = local status logic; **Zebra proposal** = the pinned node's non-committing block-proposal verdict. Local refusals are not Zebra consensus rejections.

| Test | Where rejected | Exact result |
| --- | --- | --- |
| Coordinator substitutes buyer recipient | Signer (buyer) | `buyer receive address is not this wallet's address` |
| Coordinator substitutes seller recipient | Signer (seller) | `seller proceeds address is not this wallet's address` |
| Wrong ZSA | Signer (seller); signer (buyer) | Seller: `insufficient asset_units balance: need 7, have 0`. Buyer, approving the real hash: `approved terms hash does not match these terms` |
| Changed quantity after approval | Signer (buyer); coordinator | Signer: hash mismatch. Coordinator, opening real envelopes against quantity-8 terms: `authorization envelope was issued for different terms` |
| Changed payment after approval | Signer (seller) | Hash mismatch |
| Fee amount changed in terms after approval | Signer (buyer) | Hash mismatch |
| Fee changed in the assembled transaction (6,000 zat) | Coordinator; **not Zebra** | Coordinator: `aggregate value balance is not the sponsor change`. Zebra proposal: **`VALID`**, because the fee is not covered by party signatures |
| Unrelated spend added after approval (extra sponsor coinbase input) | Coordinator; **not Zebra** | Coordinator: same mismatch. Zebra proposal: **`VALID`**; party effects were unchanged |
| Signature from wrong wallet | Signer (seller wallet run as buyer) | `buyer receive address is not this wallet's address`. A group signed by any other wallet is a different counterparty spending its own funds; group signatures carry no wallet identity |
| Modified signed buyer group (one ciphertext bit) | Coordinator; Zebra | Coordinator: `SpendAuth signature does not verify`. Zebra: `REJECTED … InvalidSignature` |
| Modified signed seller group (one note-commitment bit) | Coordinator; Zebra | Coordinator: `proof does not verify`. Zebra: `REJECTED … ConstraintSystemFailure` |
| Missing buyer authorization | Coordinator; Zebra | Envelope absent after 3 s: `Buyer authorization is unavailable`. A seller-only assembly: Zebra `REJECTED … InvalidSignature`. The party groups and the freshly signed sponsor input were unchanged, so this is consistent with the binding signature, though Zebra's error does not name which signature failed |
| Missing seller authorization | Coordinator; Zebra | `Seller authorization is unavailable`; a buyer-only assembly: Zebra `REJECTED … InvalidSignature` |
| Signer offline during execution | Coordinator | No envelope within the wait window, so no transaction was built (the buyer case was tested before signing, the seller case after the buyer signed) |
| Envelope roles swapped | Coordinator | `authorization envelope has the wrong role` |
| Reused authorization (same nonce) | Signer (buyer journal) | `terms nonce was already authorized by this wallet` |
| Second authorization while the first is live | Signer (buyer journal) | `an earlier authorization's input notes are still unspent` |
| Reused/spent input: accepted groups replayed with a fresh sponsor input | Zebra | `REJECTED … DuplicateOrchardNullifier { nullifier: 0x0287fee9…1f4f }` (coordinator-local: window closed; no local nullifier set) |
| Reused/spent input: accepted transaction resubmitted | Zebra | `REJECTED … DuplicateTransparentSpend` of the sponsor outpoint (the first check hit) |
| Reused/spent input at the signer | Signer (buyer) | `authorization window closed: next block 104, terms target 103` |
| Expired authorization | Signer (buyer) | `authorization window closed: next block 103, terms target 102` |
| Expired Action Group at consensus | **Not rejected by Zebra** | Expiry-1 group `VALID` (see the expiry gate) |
| Expired transaction at consensus | Zebra | `ExpiredTransaction { expiry_height: 103, block_height: 104 }` |
| Wrong network | Signer (buyer) | `terms are not for the disposable regtest network`. Branch-ID mismatches are covered by unit tests |
| Insufficient confirmations | Status | `PENDING_CONFIRMATIONS_1_OF_3`, then `CONFIRMATION_DEPTH_REACHED_3_OF_3_NOT_CONSENSUS_FINALITY` |
| Chain reorg | Status (simulated) | A receipt with an altered block hash, checked against the live node: `NOT_IN_BEST_CHAIN_REORGED_OR_REPLACED`. **No real reorg was induced** |
| Control: the unmodified independently signed assembly | — | Coordinator accepted; Zebra proposal `VALID` before the real submission |

Seven new unit tests in `components::independent_purchase` cover the same logic offline: term validation and arithmetic, hash binding, recipient and window checks, expected effects, journal replay rules, envelope role/terms/codec rejection, and status separation.

## Developer contract

This is a conceptual wallet/coordinator interface, implemented here only as local CLI subcommands of the disposable tool. **No HTTP endpoint, public signing API, user account, matching engine, liquidity pool or fee-sponsorship service was created.** No operation accepts or returns a seed or spending key.

| Operation | Runs in | This prototype | Returns |
| --- | --- | --- | --- |
| `getWalletBalances()` | Wallet | `wallet-signer balances` | Own native and asset balances from its own scan |
| `prepareSale()` | Seller wallet → coordinator | Seller `address` (+ `issue` in this test) supplies asset, quantity, price and proceeds address; coordinator `prepare-sale` | `PurchaseTerms` and their hash |
| `requestPurchase()` | Buyer wallet → coordinator | Buyer `address` supplies its receive address to `prepare-sale` | — |
| `reviewPurchaseEffects()` | Each wallet | `wallet-signer review` | Exact own spend, own receipt, counterparty address, fee arrangement, expiry semantics |
| `authorizePurchase()` | Each wallet | `wallet-signer authorize --approve <hash>` | `SignedGroupEnvelope` (one signed group, its native balance, commitment and binding-key share) |
| `submitSignedPurchase()` | Coordinator | `purchase-coordinator assemble` then `submit` | `PurchaseReceipt` (txid, height, RPC block hash) |
| `getPurchaseStatus()` | Coordinator | `purchase-coordinator status` | The four separate statuses |

The long-term user experience stays **Choose → Review → Approve → Receive**. These commands refuse to run unless the wallet database lives inside a private role home, and they are only exercised against the disposable regtest. They must not be exposed as a network service.

## Lineage and compliance boundary

`NormalLineageStepV2`, its circuit, relation, proving parameters and keys, the Phase 3 verifier, the frozen research, and the frozen Zebra/Orchard/librustzcash sources were not modified. As in Phase 3.5E, a received swap note built from a reference (split) note is not V2-reachable under frozen `r2c-h1/DECISION.md` and `ATTACK_ANALYSIS.md` §22. `ROOTED_LINEAGE_VERIFIED` is therefore not claimed. No KYC, eligibility, delivery-versus-payment, or "authenticated RWA" flag was added. A completed swap shows only that the pinned experimental node accepted a balanced, authorized transaction.

## Regression and release gates

| Gate | Result |
| --- | --- |
| Tx-tool `cargo fmt --all -- --check` | Passed |
| Tx-tool `cargo clippy --offline --locked --all-targets -- -D warnings` | Passed |
| Tx-tool `cargo test --offline --locked` | **18 unit tests** (7 new), **1 acceptance smoke test**, 0 doc tests: all passed. The Phase 3.5E fixed-term and coinbase-fee tests still pass |
| Tx-tool `git diff --check` | Passed |
| Live regtest | One accepted purchase, 13 signer-local and 4 coordinator-local rejections, 12 Zebra proposal verdicts, status checks, 0-hit leak audits |
| Product `lineage/scripts/check.sh` | Passed: fixture pins; **24** pinned R2B-L1/Orchard files; **7** pinned Layer-B files; Layer-B core has no network client; formatting; strict Clippy; Rust tests 10, 24, 8 and 2 passed; 5 doc tests passed; lockfile unchanged |
| `lineage/scripts/build-phase-2-matrix.py --check` | **236** unique cases, **201** agreements, **0** divergences, **34** not portable |
| `lineage/apps/api/tests/http_integration.py` | **46/46** passed, 0 failed. A7 `VERIFIED`, A4 `PENDING_FINALITY`, CKN `CHECKPOINT_TRUST_REQUIRED`. The seven demo-scenario evidence files differed only in timing fields; all 12 timing-rewritten evidence files were restored to their committed contents |
| Postman collection and environment | Unchanged |
| Phase 3.5E evidence and positive-fee construction | `docs/checkout-phase35e-results.md` unchanged; the `phase35e` branch is still at `eda8a88`; the shared fee builder keeps identical rules for existing callers |

## Changes and local commits

**NEW ZWA PROTOCOL WORK**, in the non-frozen tx-tool branch `phase35f/independent-wallet-signing`: 10 files, 2,800 insertions, 7 deletions relative to `eda8a88`.

Created:

- `src/components/independent_purchase.rs`
- `src/commands/wallet_signer.rs`
- `src/commands/purchase_coordinator.rs`
- `scripts/phase35f-independent-purchase.sh`

Modified:

- `src/commands.rs`
- `src/components.rs`
- `src/components/user.rs`
- `src/components/rpc_client.rs`
- `src/components/rpc_client/reqwest.rs`
- `src/components/transactions.rs`

Local tx-tool commits:

- `3d6a21c` Expose wallet, proposal and assembly primitives for separate signers
- `bdd9745` Define independently signed purchase terms and group envelopes
- `778421c` Add separate wallet-signer and purchase-coordinator processes
- `d9cd9e7` Add sandboxed Phase 3.5F independent purchase harness

The product checkout created only this document, in its own local commit. No files were deleted, and no dependency, lockfile, consensus rule, proof constraint, frozen source or remote state changed. The tx-tool is QED-it's third-party `zcash_tx_tool`; its README states the MIT License, and its attribution and license notices are untouched. No **PRE-EXISTING ZK-ORIGIN** source was touched. No push, merge, pull request, publication, deployment or remote change occurred.

## Known limitations and next risks

- Action Group expiry is not consensus-enforced. A leaked envelope is an executable free option until its notes are spent, and wallet-side cancellation is not implemented.
- Counterparty identity, the fee, sponsor inputs and the transaction expiry are not covered by party signatures. They rely on the sponsor's signature and coordinator policy.
- Isolation is a same-user, filesystem-level macOS sandbox on one machine. It is not hardware, OS-account or memory isolation.
- Approval was scripted, not human. Review output is CLI text, not a wallet UI.
- No real reorg was induced. Confirmation depth on regtest is not finality.
- The accepted coinbase was not freshly decoded (the node state was ephemeral).
- The received note is not rooted-lineage compatible, and eligibility is not evaluated.
- Everything here is experimental-regtest only.

Stop for owner review.
