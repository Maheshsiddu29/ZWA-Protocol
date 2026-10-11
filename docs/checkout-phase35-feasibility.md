# Phase 3.5 checkout feasibility gate (2026-10-10)

## Verdict

**PARTIAL milestone; BLOCKED for a genuine ZSA-for-ZEC purchase on the pinned
runnable stack.**
No checkout or receipt API has been enabled. No new transaction was submitted in
this phase. The Phase 3 rooted verifier and its pinned A7/A4/CKN demo remain the
working product surface. This is a timeboxed audit, not a claim that ZIP 228
cannot support an asset/ZEC swap after engineering and protocol review.

The local Orchard test constructs and verifies a ZSA/ZEC SwapBundle in memory.
That is a lower-level primitive, not a serialized transaction accepted by Zebra.
The runnable modified tx-tool constructs only two-ZSA exchanges with a shielded
ZEC matcher payment and **zero** network fee. Its sole end-to-end swap command
has neither an asset/ZEC purchase variant nor a positive miner-fee path. The
new-chain authentication pipeline required for a truthful receipt is also absent.

## Source and runtime inventory

- Starting product branch/HEAD: `integration/lineage-phase-3` at
  `2805e36392f28519e931efb21ed417787b25212b`, clean. Audit branch:
  `integration/zwa-checkout-poc`.
- Runnable swap tx-tool: QED-it `zcash_tx_tool` base
  `217b979ee01afb844190a162fb77874135aef587`, with the pre-existing Phase
  0E working-tree changes in `src/components/transactions.rs` and
  `src/commands/test_asset_swaps.rs`. Its `zcutil/fetch-params.sh` is already
  missing due to quarantine; it was not restored or run.
- Node: QED-it Zebra `0aef55cea41b83f17610e6ea708e59995f9e739f`, clean
  in `../zsa-feasibility/phase-0e-zec-fee/zebra`.
- Pinned swap dependencies: librustzcash
  `5a55da948498dd0995d0f438b4c8e9a3f0150154`, Orchard
  `d91aaf146364a06de1653e64f93b56fac5b3ca0f`, ZIP source
  `fd71419a8c5a048a520499c3f8c83ad62337fb47`.
- Earlier issuance tx-tool: `6bcf2c5e679916b9297113b533ebc6df94a0e5b6`;
  earlier issuance Zebra: `8c9c93fdd91b89fab387ec68362a93b2baca7bab`.
- Product verifier: `lineage/src/phase2.rs` loads hash-pinned research
  `SnapshotChainView`s, not live blocks. `docs/phase3-api.md` describes its
  configured A7/A4/CKN behavior. Frozen research HEAD is
  `3e7115d0e4c2f1cf36355f028447fc1b58290583`.
- Docker CLI is installed, but the Docker daemon was unavailable; the old
  `127.0.0.1:18234` node RPC refused connections. Historical experiment logs
  are evidence of the earlier run, not a fresh Phase 3.5 acceptance test.

## Capability matrix

Status vocabulary: `SUPPORTED`, `SUPPORTED_WITH_LIMITATIONS`, `BLOCKED`,
`NOT_TESTED`. A status only covers the evidence stated beside it.

- **A. Fresh issuance — SUPPORTED_WITH_LIMITATIONS.** Phase 0B and Phase 0E
  issued sample assets on local experimental regtest. Fresh issuance was not
  replayed here. The swap Zebra pin does not enforce the complete ZIP-227
  issued-note rho/authentication condition identified in
  `../zsa-feasibility/lineage-research/r2c-h1/DECISION.md`.
- **B. Fresh ZSA transfers — SUPPORTED_WITH_LIMITATIONS.** The earlier
  `test-orchard-zsa` lifecycle accepted transfers on its pinned local node;
  no fresh transfer was replayed in this session.
- **C. ZSA-for-ZEC exchange — BLOCKED at transaction level.** Orchard's
  `tests/zsa.rs::action_group_and_swap_bundle`, second case, constructs and
  verifies asset/ZEC action groups in memory. The modified tx-tool's
  `create_swap_transaction_with_matcher` takes two custom assets, adds a
  reference note for each desired asset, and has no native-payment purchase
  path. No accepted asset/ZEC txid exists in the evidence.
- **D. Buyer/seller authorization — SUPPORTED_WITH_LIMITATIONS as a
  primitive.** Each Orchard Action Group has spend signatures over its group
  commitment. The demo uses one `User::random` wallet and centrally signs for
  all accounts; no separate buyer/seller signing interface was tested.
- **E. ZEC network-fee funding — BLOCKED end to end.** Phase 0E spends native
  ZEC in Party B's group but sends it all to the matcher. The tx-tool
  `build_tx` selects a zero-zatoshi `FeeRule`; the pinned librustzcash
  `zcash_primitives/src/transaction/builder.rs::value_balance` precheck omits
  prebuilt swap groups before its fee check. Orchard's in-memory second case
  has a residual 5-unit miner fee, but no Zebra transaction test establishes
  this through the pinned builder.
- **F. Optional matcher fee — SUPPORTED_WITH_LIMITATIONS.** Phase 0E's
  earlier accepted two-ZSA swap paid 5 shielded zatoshis to the matcher.
  ZIP 228 permits matcher extraction; no exact matcher-recipient/amount policy
  commitment, quote bound, or independent signers are implemented.
- **G. One atomic settlement — SUPPORTED_WITH_LIMITATIONS.** Phase 0E's
  two-ZSA transaction settled asset legs and matcher payment in one accepted
  block; removal of its matcher group failed binding verification. An
  asset/ZEC purchase with positive network fee has not been submitted.
- **H. Independent chain confirmation — BLOCKED for new checkout.** Phase 0E
  recorded a Zebra acceptance log, block transaction list, and wallet scan,
  but Phase 3 has no live authenticated ChainView, history validator, or reorg
  persistence for new transactions.
- **I. New received-note lineage proof — BLOCKED for the candidate shape.**
  The frozen `NormalLineageStepV2` covers positive, non-native, non-split
  rho-backbone steps. A received swap asset leg built from a reference note
  is a split action and is not V2-reachable; see
  `../zsa-feasibility/lineage-research/r2c-h1/ATTACK_ANALYSIS.md` section 22.
- **J. Authenticated verification on resulting chain — BLOCKED.** Existing
  `SnapshotChainView` data are immutable; a new regtest block is absent from
  A7. No validated live chain pipeline or per-asset issuance audit exists.

## Reproduced evidence and its limits

The existing Phase 0E log at
`../zsa-feasibility/phase-0e-zec-fee/artifacts/evidence-summary.txt` records
txid `c548fed477fd8a243fd6e2fbb2e82491758496a78a3eb0ca2112b4a1d8cfe279`
at height 104, RPC block hash
`f426475a1a28e1adbb7e2ef4fa6b33af600e20b26bf2a288751db4de6f74e644`.
Party A went from 1000 to 990 Asset X and 0 to 5 Asset Y; Party B went from
0 to 9 Asset X, 10 to 4 Asset Y, and 625000000 to 624999995 zatoshis;
the matcher received 1 of each custom asset and 5 zatoshis. The aggregate
native balance and network fee were both zero. Its mutation of the matcher
group was rejected as `InvalidSignature`, with height and balances unchanged.
These values are historical and **do not constitute a ZSA-for-ZEC purchase**.

The current `../zsa-feasibility/phase-0e-zec-fee/.target/release/zcash_tx_tool --help` exposes
`test-orchard-zsa` and `test-asset-swaps`, with no checkout or asset/ZEC
purchase command. The earlier Phase 0C/0D/0E reports are indexed in
`docs/feasibility.md`. ZIP 228 specifies native fee Actions and an off-chain
order, but its matcher fee policy does not by itself cap extraction for this
product. Its primitive specification is
<https://zips.z.cash/zip-0228>.

## Security and implementation stop

An Action Group signature binds that group's exact effects, while the aggregate
binding signature binds the complete ordered group set and value balance. The
Phase 0E removal test demonstrates rejection of one post-authorization edit;
it does not establish all quote, signer, recipient, replay, and fee controls
required for checkout. The current test wallet holds all parties' regtest
keys in one process, so it is **TEST-SIGNER MODE** only. No production key or
user fund was used here.

The required purchase path stops at Priority 1. Implementing a quote or HTTP
success flag before a fee-funded, accepted asset/ZEC transaction and an
authenticated receipt would invite false settlement claims. No buyer payment
was initiated. No frontend, order book, wallet, or settlement adapter was added.

The required negative purchase tests (insufficient funding, wrong asset or
recipient, altered price/fee, expiry, replay, proof/provenance faults, rejected
transaction, finality, reorg, malformed receipt, signer failure, and node
failure) are **NOT_TESTED** as checkout cases. Phase 0E covers only one real
binding-signature mutation rejection. Existing lineage and HTTP regressions
belong to the separate Phase 3 verifier, not to a purchase service.

## Reproduction commands run in this audit

- `docker ps --format '{{.Names}} {{.Image}} {{.Ports}}'`: Docker daemon
  unavailable, including outside the filesystem sandbox.
- `curl --max-time 2 ... http://127.0.0.1:18234`: connection refused.
- `phase-0e-zec-fee/.target/release/zcash_tx_tool --help`: passed; commands
  listed above.
- `cargo test --offline --locked --test zsa action_group_and_swap_bundle
  -- --exact --nocapture` in the pinned Orchard checkout: could not resolve
  the pinned halo2 Git source from that checkout's offline Cargo cache.
- `python3 scripts/verify-r2b-l1-source.py` and
  `scripts/verify-layer-b-core-source.sh`: passed, 24 and 7 pinned files.
- `lineage/scripts/check.sh`: passed fixture hashes, source pins, Cargo
  formatting, clippy with warnings denied, unit/golden/adversarial/rooted/doc
  tests, and lockfile integrity.
- `lineage/scripts/check-research.sh`: the differential, fixture-source, and
  full Layer-B core test targets passed. The broader `phase2` test target was
  interrupted after several passing cases to respect the feasibility timebox;
  its full suite is not claimed to pass in this session.
- `python3 lineage/scripts/build-phase-2-matrix.py --check`: passed with 236
  unique classified cases, 201 agreements, zero divergences, and 34 cases
  marked not portable.
- An existing Phase 0D compiled test binary was executed with
  `--exact action_group_and_swap_bundle --nocapture`: passed, 1 test in
  217.15 seconds. Its source-build provenance was not re-established here;
  the fresh Cargo build attempt above failed offline.
- `python3 apps/api/tests/http_integration.py`: passed 46 assertions with
  zero failures when allowed to bind a temporary loopback port. The script's
  timing-only rewrites of 12 tracked Phase 3 evidence files were restored
  after inspection; their pinned contents remain unchanged. Observed A7
  first request 29.67 ms, warmed mean 26.352 ms, eight-request mean
  97.362 ms; these are **verifier fixture** timings, not purchase timings.
- Phase 3 status assertions in that HTTP run: A7 `VERIFIED`, A4
  `PENDING_FINALITY`, CKN `CHECKPOINT_TRUST_REQUIRED`; seven demo scenarios
  and the trade gate passed. Existing Postman assets were not altered.

No fresh accepted purchase transaction, block reference, receipt, lineage
proof, end-to-end performance figure, or new-chain verification result exists.
