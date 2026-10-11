# Phase 3.5E: first fixed-price ZSA-for-ZEC purchase

## Verdict and boundary

**PURCHASE_ACCEPTED** on one disposable, pinned experimental Zebra regtest. One signed transaction transferred 7 units of a specific custom ZSA to the buyer, 10,000 shielded native-ZEC zatoshis to the seller, and paid a 5,000-zatoshi network fee. The purchase block was accepted at height 103, and an independent RPC query found the transaction in that block. This is a **TEST-SIGNER MODE** demonstration: the disposable harness held both trading keys. It does not establish independent wallet signing, a production checkout, or general RWA-compliant settlement. Stop for owner review.

| Separate status | Result |
| --- | --- |
| `PURCHASE_SETTLEMENT_STATUS` | `ACCEPTED` on this pinned experimental regtest |
| `ROOTED_LINEAGE_STATUS` | `UNSUPPORTED` for the received swap note under frozen `NormalLineageStepV2` |
| `ELIGIBILITY_STATUS` | `NOT_EVALUATED` |

## Starting state and source pins

The product checkout began clean on `integration/zwa-checkout-poc` at `f075088a05a9c02cc7edb87e25d79493af142a24`. The approved non-frozen isolated tx-tool checkout began clean on `phase35d/shielded-fee-change` at `4a2c8984044920b7388f4ef6cac95e09d753f683`; this work used its new local branch `phase35e/zsa-for-zec-purchase`. The original tx-tool checkout at `217b979ee01afb844190a162fb77874135aef587` retained its pre-existing edits to `src/commands/test_asset_swaps.rs` and `src/components/transactions.rs` and deletion of quarantined `zcutil/fetch-params.sh`. It was not changed. The frozen Zebra checkout stayed clean at `0aef55cea41b83f17610e6ea708e59995f9e739f`; the disposable image was `sha256:540a3429ef14ff3c575258255e4310951606d16c86ec5e1c37e285ea95178185`, bound only to `127.0.0.1:18242`. The pinned Orchard and librustzcash revisions were `d91aaf146364a06de1653e64f93b56fac5b3ca0f` and `5a55da948498dd0995d0f438b4c8e9a3f0150154`. Frozen research stayed clean at `3e7115d0e4c2f1cf36355f028447fc1b58290583`.

The prior accepted positive-fee two-ZSA swap remains the baseline: txid `7df9e70439dd3bfe6533f4886ab1fe24de2ad00f2e9fc465ac6ad49ce9c686b6`, height 104, RPC block hash `c1293bf89a1dd1b8aea6d3dbcc2bfeb0bc5d02918211c28216f80d4df3c522ef`, network fee 5,000 zatoshis. Its independent chain and wallet evidence is in [the Phase 3.5C result](checkout-phase35c-results.md). This phase's unit test confirmed the corrected old balance expectation `[0, 1,249,994,995, 5]`; the full old command was **not** rerun, so its previously failed final reporting path is not claimed as a complete fresh run. Its transaction meaning and accepted evidence were not altered.

## Purchase shape, exact terms, and authorization

[ZIP 228](https://zips.z.cash/zip-0228) describes asset-swap Action Groups; the exact pinned implementations decide this experiment. The pinned Orchard builder supports native ZEC as an Action value: `pad_spend` supplies a native dummy note, while custom-asset padding needs a reference note. Thus native ZEC is a genuine buyer/seller swap leg here, not merely a fee input or a synthetic custom asset. The tx-tool builds and signs three groups in one v6 transaction: buyer spends native ZEC and receives the custom ZSA; seller spends that ZSA and receives native ZEC; a separate group returns shielded native ZEC change from a mature transparent coinbase used solely for the network fee. The final transaction has one transparent input and **zero transparent outputs**, satisfying the pinned Zebra coinbase-spend restriction.

| Fixed term | Value |
| --- | ---: |
| Custom asset base | `2cfa2f78c7ad1608843a2993908eeba46adeb66a3399b79cb65c73139364f90a` |
| Seller issuance before purchase | 100 units |
| Buyer purchase quantity | 7 units |
| Buyer shielded native-ZEC payment | 10,000 zatoshis |
| Seller shielded native-ZEC proceeds | 10,000 zatoshis |
| Matcher fee | 0 zatoshis |
| Network fee | 5,000 zatoshis |
| Buyer's trade-leg native-ZEC change | 624,990,000 zatoshis |
| Buyer's fee-funding shielded change | 624,995,000 zatoshis |
| Seller's custom-ZSA change | 93 units |
| Purchase target height / global transaction expiry | 103 / 143 |
| Buyer and seller Action Group expiry | 113 |

The buyer group's pre-serialization native balance was **+10,000** and the seller group's **−10,000** zatoshis; their payment and proceeds cancel exactly. The third group has **−624,995,000** zatoshis of native balance, returning change from the 625,000,000-zatoshi transparent fee input. Checked subtraction gives the 5,000-zatoshi network fee. The pinned v6 deserializer stores only the aggregate Orchard value balance and inserts zero placeholders for individual Action Groups, so individual group balances were verified before serialization and the parsed transaction's aggregate `−624,995,000` was checked afterward. No matcher payment or unrelated transparent output was authorized.

The mature fee input was height-2 coinbase outpoint `52d16b3f37b8e0a2cc1cf889b5ac7f8be6de18aef724fd601afbef1d9843cf6e:0`, with the expected 625,000,000-zatoshi value, miner script, and serialized internal-order outpoint. At height 103 it was 101 blocks old. The buyer's 625,000,000-zatoshi shielded trade input and seller's 100-unit issued note came from the local wallet scan. Before submission, the signed 28,060-byte transaction round-tripped to the same txid; all three Action Group proofs, their SpendAuth signatures, and the aggregate binding signature verified. Test-signer outgoing viewing keys recovered **exactly five intended shielded outputs**, with recipient, asset and amount matching every fixed term above. Removing either trading group while retaining the original binding signature failed local binding-signature verification. This checks the signed bundle's immutability and prevents reusing it as a fee-only extraction. The harness controls both private keys; this evidence must not be interpreted as separate buyer/seller wallet approval.

The pinned librustzcash builder sets global transaction expiry to target height plus 40. The harness set both trading-group expiries to target plus 10. Pinned Zebra transaction consensus checks the global expiry. No Action Group expiry enforcement was found in this Zebra pin; group expiry is encoded and signed, but acceptance here does **not** prove that its deadline is enforced by this node. The local `PurchaseSpec` checks distinct parties, nonnative asset, positive quantity and payment, ZEC money range, and checked group-expiry arithmetic.

## One qualified proposal and independent chain observation

The harness mined disposable zero-fee setup blocks to height 100, shielded the buyer's height-1 coinbase in block 101, and issued 100 custom-ZSA units to the seller in block 102. Three earlier fresh-node diagnostic runs stopped **before purchase submission**: missing native-reference-note handling, an incorrect assumption about global expiry, and an incorrect assumption that parsed v6 groups retained individual value balances. Each issue was fixed and the disposable chain and wallet state reset. The recorded run submitted **one** qualified purchase block.

The fee-aware block builder independently recomputed the finalized transaction fee from its mature transparent prevout and aggregate Orchard value balance. It adjusted only the miner-controlled coinbase output, preserving the regtest funding-stream/deferred treatment. At height 103 this pinned regtest has no funding-stream or deferred output: expected and accepted coinbase breakdown was 625,000,000 subsidy + 5,000 fee = **625,005,000 zatoshis** in one transparent miner output. It refroze the changed coinbase and recomputed transaction Merkle root `b8ba62852b64173fc9a9827aa85493faf5145ae98c641883153c84668245b6c7`, authorizing-data root, block-commitments hash, and header. Signed purchase bytes were not patched.

The qualified purchase proposal's exact `submitblock` response was:

```json
{"result":null,"id":"zcash-tx-tool"}
```

The transaction hash was `8e532e7bf08f86d8dc193e1081b051afb18a4071a7e76da24f68e48713cdeb06`. The tx-tool's internal-order candidate block hash was `aeb2c35ef3f7963bde070e7c7a259dd5aaf344b41cacb86b8d1a8101b77f1649`; the independent RPC-display block hash was **`49167fb701811a8d6bb8ac1cb444f3aad59d257a7c0e07de3b96f7f35ec3b2ae`**. These display orders differ, and the RPC value is used as the accepted chain hash.

An independent post-command RPC query returned best height **103**. `getblock 103` returned that block hash and listed coinbase txid `c4ab3733b7e0a766ad40d49b7ac8497c75f63cad3342073b01357cbc9daecb81` plus the purchase txid. `getrawtransaction` returned the purchase at height 103 with **one confirmation** and 28,060 serialized bytes. Independently decoding the accepted chain's coinbase raw bytes found one transparent output of **625,005,000 zatoshis**. Thus consensus acceptance, inclusion, and miner payout are independently observed; the shielded recipients and balances below come from the test wallet's scan of the accepted block, not from public commitments alone. The local run log was retained at `/private/tmp/zwa35e-purchase.log`, and the independent query result at `/private/tmp/zwa35e-independent.json`.

The accepted-block wallet scan reported buyer custom-ZSA balance **7**, seller custom-ZSA balance **93**, buyer shielded native-ZEC balance **1,249,985,000 zatoshis**, and seller shielded native-ZEC balance **10,000 zatoshis**. Relative to the pre-purchase scan, the buyer gained 7 asset units and spent 10,000 ZEC on the purchase, while also receiving the separate 624,995,000-zatoshi fee-funding change. The seller delivered 7 asset units and gained exactly 10,000 native-ZEC zatoshis. The chain's accepted coinbase reflects the 5,000-zatoshi network fee. The builder calculated these effects beforehand; the chain query and post-inclusion wallet scan are the actual observations.

## Negative controls, lineage boundary, and limits

Local unit tests reject same buyer/seller account, native asset in place of the custom purchase asset, zero quantity, zero payment, out-of-range ZEC payment, and Action Group expiry overflow. Before submission, local binding-signature checks rejected removing the delivery group or payment group. The signed-output recovery assertion would stop the run for a wrong asset, quantity, price-derived output, or recipient. The accepted transaction also passed Zebra's proof, signature, balance, maturity, and nullifier checks. These are **local negative controls** plus one real consensus positive; no additional malformed purchase block was submitted. Direct consensus rejection of tampered signatures, changed recipients/prices, insufficient funds, expired groups, and replayed inputs was not separately demonstrated. In particular, the pinned Action Group expiry boundary remains unverified.

Frozen research `r2c-h1/DECISION.md` and `ATTACK_ANALYSIS.md` §22 limit the V2-only backbone to normal, non-split, non-reference actions; received swap legs can arise through split/reference actions and are not V2-reachable. The pinned legacy Zebra swap stack also lacks the later ZIP-227 C5 issued-note ρ/authorization enforcement required by that research. Therefore the received purchase note is **not** claimed as rooted-lineage compatible. No `NormalLineageStepV2` circuit, lineage relation, proof key, parameter, Phase 3 verifier, frozen Zebra/Orchard source, or eligibility rule was changed. The later rooted L1 pin and this accepted legacy swap pin remain distinct. General marketplace signing, custody, expiry enforcement, rooted compliance, and a production wallet are separate blockers.

## Changes, validation, and owner review gate

**NEW ZWA PROTOCOL WORK:** the non-frozen tx-tool changed `src/commands.rs` and `src/components/transactions.rs`, and created `src/commands/test_asset_purchase.rs`. The latter is a disposable fixed-price command; the transaction component constructs the buyer, seller, and fee-change groups, while existing fee-aware block assembly remains the source of miner accounting and commitments. The product checkout created this document. No files were deleted. **PRE-EXISTING ZK-ORIGIN** sources, attribution, and license notices were preserved. No dependencies, lockfiles, remote refs, consensus rules, or service architecture changed. Security impact is limited to the local test harness; its two-key custody and unverified group-expiry enforcement preclude a general checkout claim.

Tx-tool `cargo test --offline --locked` passed **11 unit tests, 1 acceptance smoke test, and 0 doc tests**; `cargo fmt --all -- --check`, strict `cargo clippy --offline --locked --all-targets -- -D warnings`, and `git diff --check` passed. Product `lineage/scripts/check.sh` passed fixture hashes, 24 pinned R2B-L1/Orchard files, 7 pinned Layer-B files, formatting, strict Clippy, Rust tests, doc tests, and lockfile integrity. The 236-case differential check reported **236 unique cases, 201 agreements, 0 divergences, 34 not portable**. Existing localhost HTTP integration tests passed **46/46** assertions; generated evidence files were restored to their pre-run contents. Frozen source and research checkouts remained clean.

Local tx-tool implementation/test commit: `eda8a88` (`Demonstrate fixed-price ZSA purchase for shielded ZEC on regtest`). Product documentation commit is recorded in local product history. Before commits, the tx-tool staged diff stat was **3 files, 455 insertions, 12 deletions**; the product change was this one new document. Final branch status is clean in both isolated working trees. No push, merge, PR, publication, deployment, or remote change occurred. Stop for owner review.
