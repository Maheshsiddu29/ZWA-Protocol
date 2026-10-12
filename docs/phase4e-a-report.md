# Phase 4E-A local checkpoint report — persistent three-asset regtest

**Verdict for the full Phase 4E request: PARTIAL / BLOCKED.** This checkpoint establishes three same-chain purchases and surviving combined wallet scans in scripted test mode. It does not satisfy the requested complete exchange.

## Baselines and architecture

- Development branch: `integration/phase4e-persistent-exchange`, created locally from the clean Phase 4D worktree at `65a66f8d74e37b3b38331420e2f7bc6313e91ab5`. The original `ZWA-Protocol` checkout remains at `5275e4fd17bc3fafbbb22d7137c40269f195cd4f` with its pre-existing untracked files untouched. The detached UI review worktree remains untouched.
- **NEW ZWA PROTOCOL WORK:** `exchange.persistent_chain` runs the exact pinned Zebra image with `ephemeral = false` and a private on-disk bind mount, then checks its genesis and saved block anchor before reporting an active or restarted chain. It keeps the node separate from wallet homes and seeds. `phase4e-a-scripted-validation.sh` parameterizes the Phase 4C.1 checkout across three distinct issued assets without modifying the frozen proof circuit or pinned tx tool. `exchange.portfolio_scans` verifies each accepted quote, authorization result, per-trade scan, and a fresh combined scan before recording private holdings. `phase4e-a-rescan.sh` repeats the wallet scan after later restarts.
- **PRE-EXISTING ZK-ORIGIN** Poseidon/Merkle lineage and all prior source attribution remain unchanged. No frozen Halo2 or Groth16 baseline, consensus code, or historical artifact was edited.

## Real scripted run on one chain

The passing private root is `/private/tmp/zwa4e-phase4e-b`; container `zwa-phase4e-b`; loopback Zebra RPC port `18321`. All evidence in this section is **SCRIPTED_TEST_MODE**, not human approval. The common genesis hash is `029f11d80ef9765602235e1bc9727e3eb6ba20839319f761fee920d63401e327`. The accepted purchase anchor is block 113, hash `232637e450e8602079cec68cd53b44c0dd2649168a85022ca79fd7e860d5c990`.

- Gold-reference experimental unit: AssetBase `da11b98079a035182d74c4f9825999ff604b301274f757de8c3da88c55708890`; issuance txid `4b2214d1f33566f6bc7d47abed01ef8641d919bd4e71c6fd8f246a11775e4429`; 7 units for 10,000 zatoshis; purchase txid `2e93a7c23645c018f1d079a3402d5b63903da985c4e92e2374bc258db989e785` at height 105.
- Treasury-style experimental unit: AssetBase `1888cc2d829a7746cc59d7386c29cabdb2189de9fda898d2d410d9a76fe57bba`; issuance txid `d6a7a9abc56b7f115730416e1bfc8a74940382b6238b6194e2c0479d19faa34d`; 5 units for 12,500 zatoshis; purchase txid `33f2ac2ffe04fffc3abc9f344e3de6043413a6644df2b16f6fb3851d999bb646` at height 108.
- Credit-participation experimental unit: AssetBase `ae01ee144164b0e6f5aa3a40e47695f729006ca5cce11cf316c8bf69677c52a5`; issuance txid `d6dd8f4431f2d935ceee7a3d23c8b5408463b3eb7029428f5ee125092d70d6ba`; 3 units for 8,500 zatoshis; purchase txid `a454ab9592378693fd76ec4c1252f918b481a5e73871588b5d3ba22ea87b1724` at height 111.

The same buyer seed scanned 7, 5, and 3 units, respectively, after each transaction and again at height 1,114 after the node restarted twice. Its scanned native balance was 624,969,000 zatoshis after the three purchases. The same seller seed scanned 93, 95, and 97 remaining units and 31,000 native zatoshis of cumulative proceeds. The three per-trade Groth16 outcomes reported verified issuer authentication, issuance provenance, credential authority, investor eligibility, and exact trade authorization. The verifier still reports purchased-note Halo2 lineage as unsupported. Each receipt was independently found in its accepted Zebra block. Wallet leak-audit checks found zero seed, spending-key, or viewing-key matches in the operator output scanned by the run.

## Restart finding and fail-closed negative result

The first private run at `/private/tmp/zwa4e-phase4e-a` issued and purchased all three assets, then stopped the node while the purchase blocks were still non-finalized. On restart, pinned Zebra retained only height 14. The saved height-113 anchor was absent. The manager returned `CHAIN_RESET`, persisted that determination, and refused to promote the earlier wallet scans as current. That failed run is preserved as diagnostic evidence; its node is stopped.

The second run mined 1,100 additional empty experimental blocks after the purchases. After the first restart the node reloaded height 1,114, with all three purchase blocks and wallet holdings intact. A second stop/start and independent rescan returned the same holdings and seller proceeds. This tests persistence on the pinned experimental image. It does not imply irreversible finality or protection against an operator replacing both the chain database and its local identity file.

## Validation and current limits

- Real integration: three distinct issuance txids, three accepted purchases on the same chain, three Groth16 gates, separate buyer/seller scripted signer paths, independent block and transaction RPC checks, per-trade scans, combined scans, and two successful restart rescans.
- Tests: `python3 -m unittest exchange.test_persistent_chain exchange.test_exchange checkout.test_checkout` passed 19 tests. `bash -n` for both new scripts and Python bytecode compilation passed. The existing checkout HTTP test requires loopback socket permission; the sandbox-only run was denied, and the same suite passed with loopback access.
- No new external dependency. No remote state changed. No secrets or private databases were added to Git.
- Pricing is not oracle-executable: the three ZEC payments were fixed test terms. No live Pyth XAU/USD or authenticated ZEC/USD conversion was used, and no accepted price snapshot is bound into the proof. The signed NAV adapter was not exercised in this run.
- Issuance still originates from the seller test wallet; an independently authenticated issuer issuance portal and separate issuance wallet remain open. The website still has only the previous one-session portfolio view. The new combined scan is an operator-private file, not yet a role-scoped browser portfolio.
- The remaining Phase 4E checkpoints include durable offers/orders, price commitment V3 as needed, wallet companion and human consent UX, four-role resource privacy, complete browser flow, RFQ rooms, lineage research, all-category fixtures, negative acceptance, and full repeat-run operator validation. None is claimed complete here.

**Do not label this checkpoint `COMPLETE_EXPERIMENTAL_EXCHANGE`.**
