# Phase 3.5B: transparent txid conversion and positive-fee swap

## Result

**REJECTED_WITH_CONSENSUS_REASON: `Transaction(Subsidy(InvalidMinerFees))`.** The non-frozen tx-tool RPC conversion defect is fixed and tested in local tx-tool commit `599be2f647ce19982c45eb18cbf52801f38fdac7` on branch `phase35b/txid-conversion`. The accepted zero-fee control was reproduced on an isolated Zebra regtest chain. The corrected positive-fee transaction passed local preflight, but Zebra rejected its block. The node remained healthy; neither the block nor transaction entered the best chain. **`POSITIVE_FEE_SWAP_ACCEPTED` was not reached, and checkout development must not resume.** No second positive-fee submission was attempted.

## Root cause and byte order

Zebra's `getblock` RPC returns a 64-character, display-order transaction ID. The pinned `TxId` stores 32 internal bytes and formats them by reversing those bytes. A transparent `OutPoint` writes the internal bytes directly. The old RPC adapter decoded the displayed hex and passed it unchanged to `TxId::from_bytes`, then `get_block(2).tx_ids[0]` entered the swap builder's transparent input. This put display-order bytes into the serialized outpoint. The adapter's old `get_transaction` path sent internal bytes to RPC, which masked the mismatch in some lookups. `send_transaction` decoded its RPC result the same incorrect way.

The actual height-2 coinbase fixture from the Phase 3.5A isolated Zebra regtest is:

- Parent block RPC hash: `f95e67ffb134d937512db74434395bb6eef71e0e14acfe7d96a46263691824b9`.
- Canonical RPC/display txid: `52d16b3f37b8e0a2cc1cf889b5ac7f8be6de18aef724fd601afbef1d9843cf6e`.
- Correct 32 serialized outpoint hash bytes: `6ecf43981deffb1a60fd24f7ae18dee68b7facb589f81ccca2e0b8373f6bd152` (followed by little-endian index `00000000`).
- The failed Phase 3.5A candidate serialized `52d16b...843cf6e` at that outpoint instead. Zebra's verifier could not locate that UTXO and waited in the missing-UTXO path; the client timed out. The node stayed alive and never committed that block. See [the Phase 3.5A diagnostic](checkout-positive-fee-submission-diagnostic.md).

The conversion now happens only at the non-frozen RPC boundary: `txid_from_rpc_display` strictly decodes exactly 32 bytes and reverses once into `TxId`; `txid_to_rpc_display` uses `TxId` formatting for outbound RPC. `get_block`, `send_transaction`, and `get_transaction` use those helpers. `OutPoint` and Zebra serialization remain unchanged. No frozen research, consensus, cryptographic, proof, fee-rule, or dependency source was edited.

## Source and fixture tests

The sole committed tool source change is `src/components/rpc_client/reqwest.rs` in the non-frozen `zsa-feasibility/phase-0e-zec-fee/tx-tool` repository. Its three new tests check the real height-2 coinbase fixture, the serialized and parsed outpoint, display round-trip, malformed hex and length rejection, and that the old display-order outpoint cannot silently name this coinbase. The transaction-tool base was `217b979ee01afb844190a162fb77874135aef587`; the local repair commit is `599be2f647ce19982c45eb18cbf52801f38fdac7`.

The isolated transaction-tool tests passed (3 unit tests and 1 acceptance smoke test), as did `cargo fmt --all -- --check` and `cargo clippy --offline --locked --all-targets -- -D warnings`. The disposable positive-fee probe additionally passed an insufficient-funding builder test. An invalid reversed outpoint was confirmed to be a different identifier and did not resolve through Zebra RPC. These are builder and lookup checks, not claims of consensus rejection. The disposable probe added only diagnostic assertions and logging in `/private/tmp/zwa35a-tx-tool/src/commands/test_asset_swaps.rs`, `src/components/transactions.rs`, and `src/components/rpc_client/reqwest.rs`; those changes are not committed tool behavior.

## Pinned regtest and zero-fee control

The disposable Zebra regtest used `qedit/zebra-zsa-swap-pinned:latest` at image ID `sha256:540a3429ef14ff3c575258255e4310951606d16c86ec5e1c37e285ea95178185`, on loopback port `18238`, with NU5, NU6, NU7, and SWAP active at height 1. The accepted zero-fee control used the original Phase 0E binary on this same isolated chain. Zebra returned `submitblock` result `null` for the valid block, and an independent height query found transaction `575436efbbc3f723c82778d3848181f835ff06e20155e947e64ac143b3af76af` at height 104 in RPC-display block `474993b623af74d1e545ff912cc3c6ef52ae014d487c3446c742978f7eff0f1d`. Its network fee was zero. It exchanged asset X `10 = 9 + 1` and asset Y `6 = 5 + 1`, and transferred five shielded zatoshis to the matcher. The removed-group negative control returned `InvalidSignature` and was not accepted.

The first observable difference from the zero-fee control is the transparent input and its 5,000-zatoshi network fee. The zero-fee block could use the template coinbase unchanged. The repaired probe proved that its signed serialized outpoint referenced the actual mature height-2 coinbase before `submitblock`. Its block assembly still copied the template coinbase unchanged; that first block-accounting divergence caused the new rejection.

## Positive-fee candidate and accounting

The candidate used the same chain and pinned stack, one mature height-2 coinbase input of `625,000,000` zatoshis, one transparent change output of `624,995,000` zatoshis, and a `5,000`-zatoshi network fee. Three prebuilt Orchard Action Groups conserved custom assets as X `10 = 9 + 1` and Y `6 = 5 + 1`; their native aggregate was zero. Separately, Party B's shielded native input `625,000,000` returned `624,999,995` to Party B and `5` to the matcher. The signed transaction was parsed back without mutation. The funding transaction was fetched by canonical RPC ID, and its output-zero value and script matched the intended input. The serialized outpoint had internal-order hash bytes `6ecf...bd152` and index zero. Candidate height 208 was 206 heights after the coinbase's height 2, exceeding the 100-block maturity requirement. The transaction had one transparent input and one transparent output; no duplicate transparent spend was present in it.

| Observation | Value |
| --- | --- |
| Positive transaction hash | `44144a26e21edde1cf521b172e75c4289327f7fe93c85866e480b10125dc9b6a` |
| Candidate block hash and height | `9c8ef988d660b6847d8c4130fa6cd599f9845d2b139d1b1b1e48fbc35fab6872`, height 208 |
| Parent and node tip before submission | Height 207, `600fdf450be577488db84a4f22ad18de7823ed923df173c774b3db85edf60c4f` |
| Serialized candidate | 36,071 bytes; `/private/tmp/zwa35b-positive-candidate.hex` SHA-256 `65ba52e9ed03a267366ceaba57ccdcb587faf53d17f89980c740678d4cc97210` |
| RPC timeout and result | 20-second HTTP timeout; `submitblock` returned `"rejected"` in about 0.13 seconds on the sole positive-fee submission |
| Zebra validation result | `Block { source: Transaction(Subsidy(InvalidMinerFees)) }` for this hash and height |
| Independent chain query | Tip remained height 207 at the same hash; `getblock` returned `Block not found` and `getrawtransaction` returned `Transaction not found` |

The captured block's parsed v4 coinbase had one transparent output of exactly `625,000,000` zatoshis. The candidate transaction added `5,000` zatoshis of miner fees, so the expected coinbase total at height 208 was `625,005,000` zatoshis, assuming no other fees. The pinned `template_into_proposal` function copied `block_template.coinbase_txn.data` into the block without incorporating the added transaction's fee. Zebra's pinned `miner_fees_are_valid` check requires exact coinbase input/output equality from NU6 onward; its log identifies this exact check as `InvalidMinerFees`. The client exited with code 101 because its test command called `.expect` on the rejected block. That was a tool panic after the RPC verdict, not a Zebra crash.

The pinned builder's `value_balance()` and `get_fee()` omit prebuilt swap Action Groups. The probe keeps their aggregate native balance zero and funds the requested fee from a distinct transparent pair. This avoids relying on the incomplete builder accounting for this one experiment; it does not repair the builder for arbitrary shielded fee funding or a later purchase flow.

## Validation and limits

The product Phase 3 checks passed: source and fixture pin checks, Rust formatting, strict Clippy, tests and doc-tests, and the protocol/API test suite (46/46 HTTP integration cases). The frozen research tree stayed clean at `3e7115d0e4c2f1cf36355f028447fc1b58290583`. The 236-case differential accounting matrix remained 236 unique cases, 201 agreements, zero divergences, and 34 not portable. The tx-tool dependency lockfile remained unchanged. The original tx-tool checkout has pre-existing local edits and a quarantined `fetch-params.sh` deletion; the repair was committed from a separate worktree without touching those edits or restoring that script.

Compatibility risk remains at the RPC boundary for callers that may have manually treated `TxId` internal bytes as display bytes. The three corrected methods and fixture tests cover the known path. Positive-fee consensus acceptance, later proof/signature validation stages, broader fee accounting, and any ZSA-for-ZEC purchase remain unproven. A further non-frozen block-proposal adapter would need to account for the inserted transaction's fee when constructing the coinbase and recompute affected commitments before submission. That proposed change has not been applied or validated; it changes consensus-sensitive block bytes and needs owner review. The txid conversion itself is a safe localized repair, but no accepted positive-fee swap has been demonstrated.

## Local commits and next experiment

- Tx-tool branch `phase35b/txid-conversion`: `599be2f647ce19982c45eb18cbf52801f38fdac7` — RPC txid conversion and fixture regression tests.
- Product branch `integration/zwa-checkout-poc`: this diagnostic document (commit recorded below).

Stop at this consensus rejection. A separate owner-reviewed experiment may adjust only the non-frozen block proposal's coinbase fee accounting, regenerate the block commitments, and test the result on a fresh disposable chain. Do not proceed to a ZSA-for-ZEC purchase or checkout work from this result.
