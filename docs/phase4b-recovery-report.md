# Phase 4B live checkout recovery — 2026-10-12

## Status and exact path repair

**SCRIPTED SETTLEMENT VALIDATED; HUMAN APPROVAL PENDING.** The trusted data home is `/Users/mahesh/Desktop/Colosseum/ZWA/zsa-feasibility/.local/share`. Its `ZcashParams` directory contains `sapling-spend.params` (47,958,396 bytes, SHA-256 `8e48ffd23abb3a5fd9c5589204f32d9c31285a04b78096ba40a79b75677efc13`) and `sapling-output.params` (3,592,860 bytes, SHA-256 `2f0ebbcbb9bb0bcffe95a397e7eba89c29eb4dde6191c339db88570e3f3fb0e4`). Both match the pinned values in `zsa-feasibility/REPRODUCE.md`. Neither file was downloaded, regenerated or modified.

The stale link `/Users/mahesh/.local/share/ZcashParams` points to nonexistent `/Users/mahesh/Desktop/zsa-feasibility/.local/share/ZcashParams`. The earlier scripted attempt inherited that default lookup. The repair is to supply `PARAMS_DATA_HOME=/Users/mahesh/Desktop/Colosseum/ZWA/zsa-feasibility/.local/share`; both launchers pass it to the signers as `XDG_DATA_HOME`. This makes the stale global link irrelevant, avoids mutating the user's home, and keeps the path configurable. Both Phase 4B launchers now fail before wallet creation on missing or hash-mismatched parameters.

Pinned tx-tool HEAD remained `3dd2a010fd557ef15a68915e42fec8289dc7d7d2`; pinned Zebra image remained `sha256:540a3429ef14ff3c575258255e4310951606d16c86ec5e1c37e285ea95178185`. Neither source nor frozen circuit baseline was changed.

## Fresh scripted executions

Both runs used new private `/private/tmp/zwa4b-scripted-recovery-20261012-*` wallet homes and isolated Zebra containers. Each mined 100 blocks, funded the buyer, issued a fresh 100-unit ZSA, mined the issuance, generated Sapling transaction proofs, prepared a 7-ZSA-for-10,000-zatoshi quote, and used both signers' explicit `--scripted-test-mode` flag. Both transactions were checked against independent `getblock` and `getrawtransaction` RPC results, and both wallets rescanned. The roots contain private wallet material and must remain local.

| Run | Fresh issued AssetBase | Quote digest | Accepted txid | Block hash at height 103 |
| --- | --- | --- | --- | --- |
| A, pinned Phase 3.5G scripted runner | `5d773e687da5a77d83193b953823c4b936ac35b7ffb85e4f4a6fce9954cbb806` | `4ac003476633d027fe07ed18509f0953a35e3185a138d18788eb49fefde29857` | `b6449abd2d665cb2f3e56ff1be50b8186468a5079c331c7c393dbbe0646323f0` | `7c61a8d6776366b2da015b60afd57ef334e2686b2c00f0b3da695a033a2eca85` |
| B, Phase 4B scripted observation runner | `1dc7b803307283d2577fcc03cdd9e448982a9ed412a95536db5cd6ef0a160e99` | `8eb9585101eabd1800055568ce86bd5fe995741af0a970730174772c2a30d557` | `73fd95aebe97ed0145b6992cef84eee631a8014f6ff8a537258af7690dfb4725` | `36022a43eb0155710a1704beee21ba8a49cb69caffb4bd37e25486803d25a4a7` |

For each run, actual buyer scans changed from 625,000,000 to 624,990,000 native zatoshis and 0 to 7 asset units. Actual seller scans changed from 0 to 10,000 native zatoshis and 100 to 93 asset units. The sponsor paid the 5,000-zatoshi network fee; matcher fee was zero. Run B's `public-scans.json` passed session, asset, height, and before/after reconciliation. Its API exposed nine ordered operator events, `approvalEvidence: SCRIPTED_TEST_MODE`, `status: FINALIZED` at three observed confirmations, and a receipt with `purchaseSettlement: ACCEPTED`, `rootedLineage: UNSUPPORTED`, `investorEligibility: NOT_EVALUATED`, and finality `DEPTH_3_REACHED_NOT_CONSENSUS_FINALITY`. Three confirmations are a local display threshold, not irreversible finality.

The local Next.js production site on port 3211 proxied `/api/checkout/active` to run B's API on port 18747 with an identical session and receipt. Browser inspection of `/demo` visibly showed `SCRIPTED TEST SESSION`, the fresh txid, block hash, 0 → 7 buyer ZSA and 0 → 10,000 seller zatoshis. It explicitly said no owner typed an approval and marked purchase lineage unsupported. The separate pinned research verifier was unavailable during that browser inspection; its historical fixtures were not used to authorize the purchase. The live API and test node stop after the observation window, so later page visits must not present that old session as live.

## Cryptographic status at construction time

1. **Real ZSA issuance:** checked on the fresh regtest chain. The seller issued 100 units, the issuance transaction was mined, and the seller wallet scanned the balance.
2. **Real checkout and wallet settlement:** checked in both scripted runs by transaction inclusion, independent RPC checks, and buyer/seller wallet scans. No human-approved Phase 4B transaction exists yet.
3. **Groth16 authorized-issuance proof:** not generated or checked for this fresh asset before transaction construction. The Sapling spend/output proofs used by the experimental transaction are separate from the ZWA authorized-issuance relation.
4. **Groth16 investor-eligibility proof:** not generated or checked for these fresh wallets before transaction construction.
5. **Same-trade commitment enforcement:** the exact quote digest binds local off-chain signer/coordinator terms, but no byte-equal `TradeCommitmentV1` proof signal is enforced against this settlement. The final combined transaction is not fully bound by the parties' Action Group signatures.
6. **Halo2 rooted note lineage:** independent pinned research fixtures and verifier tests passed, but no proof ties this new asset's input notes to an authenticated issuance root. It was not a construction gate.
7. **Purchase-output lineage support:** unsupported by the frozen normal lineage relation for this swap output; no same-chain output lineage proof was generated or enforced.

The result is a real **scripted, ungated experimental regtest checkout**, not FULL E2E cryptographic compliance. PRE-EXISTING ZK-ORIGIN and QED-it work is distinct from NEW ZWA PROTOCOL WORK; no research fixture authorizes either fresh checkout.

## Human execution handoff

Prerequisites: macOS with Docker running, `sandbox-exec`, `sqlite3`, `curl`, `rg`, Python 3, the pinned tx-tool binary, the pinned Zebra image, the parameter directory above, free loopback ports, and a real interactive terminal. A disposable human-mode preparation reached the **buyer signer prompt** with quote digest `b2736f22c4918fa9294a63fc5b1da2cdb5f55796d5de7d5d7a44ffdfc5592e06`, asset `7358e6178613bd6affe4364948288d72aea4b681e07d19997f90da90ec842823`, and root `/private/tmp/zwa4b-owner-run-001`. No approval input was sent. The coordinator then timed out with `Buyer authorization is unavailable`; the session result is `FAILED`, with no receipt or approval socket. That expired run was stopped after the timeout and must not be resumed. Start a different fresh session in the owner's own terminal with these exact commands from `/Users/mahesh/Desktop/Colosseum/ZWA/ZWA-Brand-Integration`:

```sh
test ! -e /private/tmp/zwa4b-owner-run-002
PARAMS_DATA_HOME=/Users/mahesh/Desktop/Colosseum/ZWA/zsa-feasibility/.local/share \
ROOT=/private/tmp/zwa4b-owner-run-002 \
PORT=18258 API_PORT=18748 CONTAINER=zwa-phase4b-owner-002 \
./checkout/operator/phase4b-human-demo.sh
```

In a separate terminal, show the website using the existing successful build:

```sh
cd /Users/mahesh/Desktop/Colosseum/ZWA/ZWA-Brand-Integration/website
ZWA_CHECKOUT_URL=http://127.0.0.1:18748 ./node_modules/.bin/next start -H 127.0.0.1 -p 3212
```

Open `http://127.0.0.1:3212/demo` for the owner's fresh run. The owner must review the buyer and seller wallet prompts and type `APPROVE <exact digest displayed by each signer>` directly into each signer prompt. Empty input or anything else rejects. Do not pipe input, set `SCRIPTED_TEST_MODE`, or reuse the scripted run's wallets. The human launcher itself has no scripted signer flag. After the owner chooses both approvals, capture the new receipt txid, block hash and height, before/after wallet scans, API events and receipt, and the website's live state. Do not disclose private roots, seeds, spending keys, full viewing keys, or signed Action Groups.

## Validation and remaining gates

The checkout Python suite passed 11/11, including negative event-mode relabeling and existing quote, session, scan, chain, replay-like, and browser security cases. Python compilation and `bash -n` passed. Website TypeScript lint and production build passed. Missing and hash-mismatched parameter directories each returned exit code 2 before creating wallets. `lineage/scripts/check.sh` passed source/fixture pins, formatting, strict Clippy, tests, documentation tests and unchanged lockfile. `lineage/scripts/check-research.sh` passed five differential tests, one fixture-source test, one full Layer-B test, and 11 Phase 2 tests (one manual fixture-regeneration test ignored). The 236-case matrix check found 201 agreements, zero divergences, and 34 not-portable cases. After the expired human preparation stopped, the website correctly changed to `NO LIVE PURCHASE` / `BACKEND_UNAVAILABLE`, without showing a stale receipt. The standalone human launcher has not been authorized by the owner, so there is no human txid or human receipt. No push, merge, deployment, publication or PR occurred.
