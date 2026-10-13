# Public ZSA Testnet Phase 2: isolated laboratory completion

**Status: PASS for the isolated laboratory; public network work is unapproved and unexecuted.** Observed 2026-10-12. This is **NEW ZWA PROTOCOL WORK** using **PRE-EXISTING community tooling**; it is not ZWA's Phase 4E atomic purchase, Groth16 settlement, Zcash mainnet, or a public testnet transaction.

## Source and network pins

| Component | Pinned revision or identity |
|---|---|
| ZWA branch baseline before this report | `integration/public-zsa-testnet` at `219b0695d9218d18b59d562b487936a391fabe43` |
| Community Zebra node and `zebra-chain` parser | `zcash-shielded-assets/zebra@6a91831a6a9c18f667f59ab956c9437bf0bb5c5f`, `internal-miner,tx_v6`, NU7 branch `0x77190ad8` |
| Lab lightwalletd | `zcash-shielded-assets/lightwalletd@b657286c583222670e01a41b296cbec8a040b963` |
| Wallet library | `hhanh00/zkool2@51df4f0b356a886d5d2308811f215366ab648d65` (`rlz`, MIT) |
| Wallet Orchard / librustzcash | `zcash-shielded-assets/orchard@60697dd60ecbc14c7d8953316cea479944e605b8` / `zcash-shielded-assets/librustzcash@53813b3a76546eabf2d1260d89436be7492a52dd` |
| Node Orchard / librustzcash | `zcash-shielded-assets/orchard@ed66d7e71d80edf5b4f62802729ee4da16bebd0e` / `hhanh00/librustzcash@09c0ea88cd6ed93160cadb2406176baac0b1338c` |
| Shared genesis; separate chain identity | Genesis `03536e258babad397d59ce2995d9015d0791ac5272b8348e8f07f94143a72bb7`; **lab block 1** `0235e4311200276186238a8441c5bf44d4027055cb374da10b75f86fc0166d21`; **public block 1 pin** `01747c32a6bd1bbfe5d91566d26c190719181908fa7560d9ef89da2204e82274` |

The differing block 1 hashes are essential: the lab uses the public network's consensus parameters and genesis but mines a private fork with no initial peers and loopback listeners at `127.0.0.1:19232` (Zebra RPC) and `127.0.0.1:19067` (lightwalletd). The lab root is `/private/tmp/zwa-pub-lab-run1`, owner-only. No public root was created. The lab node's version string cannot attest to its own source commit; the locally built binaries and Cargo locks establish the source pins used in this rehearsal.

## Acceptance and exact evidence

The first rerun reached a genuine included issuance, then stopped because the wallet fork's V6 reader presented ZSA actions as vanilla Orchard. That was a real transfer verification failure. The inspector was repaired to use the original 612-byte ZSA ciphertexts and reconstruct the recipient's action for viewing-key decryption. The second rerun of `public-testnet/lab/lab-acceptance.sh` passed. Its scratch evidence is `/private/tmp/zwa-pub-lab-acceptance-phase2-rerun/`; wallet databases and private observations remain outside the repository.

| Fact | Issuance | Shielded transfer |
|---|---|---|
| Node-native txid | `d87192ff02bb0c380b0c8ad2d6f898c50dce75f406d6d8ce48d48f1226e22ff9` | `752bf9dce3f67e350f2e7b2eebc4c23435652eb0e6ebf77e30ce0002c9f347ff` |
| Active-chain block | 666, `00fa72179cd013e049a86f0d89093c0322473766bfd8800c54d9e0804cb9a7a0` | 671, `04cce41dfac91e5d0f764489dfdd320be53df9b7aa6f376801f2f5ef7b98f9b1` |
| Observed confirmations | 10 at tip 675, 2026-10-12 23:41:15 UTC | 5 at tip 675, 2026-10-12 23:41:16 UTC |
| Fee; node conventional minimum | 20,000; 20,000 zat | 15,000; 15,000 zat |
| Orchard actions; transparent inputs / outputs | 2; 0 / 0 | 3; 0 / 0 |
| Prepared-byte SHA-256 | `48594c58a216bf22751d07b6943f8353ec5cb2608bc552d8b240383570be7f1b` | `312619b9b2afc26ae9fd5109a0138caed1da93e6d70da8a826f4175ae71ff4bd` |

The asset is `f6f9859b66e5ba8b31c94734a26e184376e3bb26ff2838d2acbdae5e92af998b`. The node's `zebra-chain` parser independently derived this AssetBase from issuance validating key `00997589fe4ab6a003e992dab4a6a17b4b5675172b9455718b3e20dbec8358c35b` and description hash `608496452e10e0ea81f0fac37ca0b98af7237e9e864034b02e40465a0efb46d3`; both match the issuance plan. The issuance is finalized and creates 1,000,000 units. The issuer wallet scan observed that asset. The recipient's viewing key decrypted **exactly 250,000 units before submission**, and its post-block scan reported 250,000. These wallet observations are private and are not inferable by public observers.

For each transaction, loopback lightwalletd `GetTransaction` returned bytes with the prepared-byte SHA-256, and its compact block contained the canonical txid. Separate Zebra RPC calls to `getrawtransaction(txid, 1)`, `getblockhash(height)`, and `getblock(hash, 1)` confirmed the same bytes, txid, active-chain block hash, height, and block membership. Zebra's `orchard.valueBalanceZat` independently reported 20,000 and 15,000, matching the respective fees; `vin` and `vout` were empty. The issuance inventory reconciled as issuer 750,000 + recipient 250,000 = 1,000,000. Issuer Orchard tZEC went from 1,000,000 to 965,000 zat: 20,000 issuance fee + 15,000 transfer fee. Recipient tZEC stayed zero. Both final wallets had zero transparent and Sapling balance, and both prepared public-transaction shapes had zero transparent inputs and outputs. The lab's separate 20,000-zatoshi funding top-up and its fee are outside this issuance/transfer accounting window.

A mid-body byte flip was still accepted by the format-only parser, then rejected by the **lab node's full consensus validation**: JSON-RPC error `-25`, ending in `could not validate orchard proof`. The corrupted transaction was never included. Invalid version group bytes, an unprefixed asset name, an over-balance transfer, duplicate asset name, and repeat submissions of **both** txids were refused. The submitted markers are written before sending, so an uncertain send result requires manual review rather than an automatic retry.

### Wallet-library versus node txid discrepancy

The wallet fork's `read_zsa` stores the original ZSA ciphertexts separately and maps the V6 Orchard actions into `OrchardVanilla`. Re-serializing that parsed object changes the single `enableZSA` flag byte from `0x07` to `0x03` directly before the anchor. Its transaction hash therefore differs from the hash of the original signed bytes. For this run, the wallet-library txids were `15bc840e3ec63f64ee3f7ea58ac2e96aae7cf3ae8c818dee50125e11f0c6612b` (issuance) and `af4309bdbd28353a5a3e286e2a10d438a7e388a5f888567d2285f5d984f4ae34` (transfer). The node parser's txids in the table are canonical: they match Zebra RPC and included block entries. The inspector allows only this identified one-byte read/rewrite difference, requires the original bytes to round-trip through the node parser unchanged, and refuses any unexplained txid difference. For transfers it also requires the **node parser's** `enableZSA` flag, so ordinary Orchard data cannot be mistaken for ZSA. This is a wallet-library representation defect, not evidence that the lab node accepted a different consensus transaction.

The recipient check takes action order and 612-byte ciphertexts from the pinned wallet reader, which obtains them with bounded `read_exact` operations in the community V6 parser. It requires one ciphertext per action and exactly 612 bytes each; reconstruction replaces only the note ciphertext domain while preserving each action's nullifier, key, commitment, value commitment, and ordering. The independently pinned Zebra parser must byte-round-trip the same original transaction before the recipient decrypts it. Any malformed length, unsupported round-trip difference, missing node `enableZSA` flag, failed reconstruction, or decryption amount/asset mismatch fails preparation. This compatibility path is specific to the pinned community format and must be reviewed again if any pinned wallet or node revision changes.

## Reproduction and validation commands

The successful stateful run was:

```sh
cd /Users/mahesh/Desktop/Colosseum/ZWA/ZWA-Brand-Integration
ROOT=/private/tmp/zwa-pub-lab-run1 \
  SCRATCH=/private/tmp/zwa-pub-lab-acceptance-phase2-rerun \
  public-testnet/lab/lab-acceptance.sh
```

Do not replay that command on the existing root: it prepares a new asset name and issues new units. To repeat end to end, create a new `/private/tmp/zwa-pub-lab-*` root and separate node state and wallets, use the loopback-only lab configuration, mine and mature lab faucet coinbase before `lab-shield`, fund a fresh issuer, then run the script with a new scratch directory. Preserve the saved Phase 2 root and its receipts for review. The independent read-only checks on the completed root can be repeated without issuing anything:

```sh
public-testnet/lab/lab.sh rpc getpeerinfo '[]'
public-testnet/lab/lab.sh rpc getrawtransaction '["d87192ff02bb0c380b0c8ad2d6f898c50dce75f406d6d8ce48d48f1226e22ff9",1]'
public-testnet/lab/lab.sh rpc getrawtransaction '["752bf9dce3f67e350f2e7b2eebc4c23435652eb0e6ebf77e30ce0002c9f347ff",1]'
public-testnet/lab/lab.sh rpc getblockhash '[666]'
public-testnet/lab/lab.sh rpc getblockhash '[671]'
public-testnet/lab/lab.sh rpc getblock '["00fa72179cd013e049a86f0d89093c0322473766bfd8800c54d9e0804cb9a7a0",1]'
public-testnet/lab/lab.sh rpc getblock '["04cce41dfac91e5d0f764489dfdd320be53df9b7aa6f376801f2f5ef7b98f9b1",1]'
```

Validation used `cargo build --release --locked` in each public-testnet crate with isolated target directories; both builds passed. `cargo test --locked` passed in both crates (13 client tests, 1 node-check test). `cargo clippy --locked --all-targets -- -D warnings` passed in both crates after fixing one new lint. `bash -n` on the lab scripts, `npm run lint`, `npm run build`, and `git diff --check` passed. The website has no separate test script; its lint script is TypeScript `tsc --noEmit`. The generated static `/testnet` HTML was inspected for both actual txids, both block hashes, the `ISOLATED LAB` label, and absence of wallet addresses, private balance fields and the 250,000-unit private transfer amount. The node RPC `getpeerinfo` returned `[]`; process socket inspection showed only loopback listeners and no external TCP connections. `cargo fmt --all -- --check` reports extensive pre-existing formatting differences across the original client and node-check sources, including files untouched by Phase 2; `rustfmt --check` on the `HEAD` version of untouched `chain.rs` also failed. The Phase 2 changes did not reformat those unrelated files.

## Privacy, safety and trust boundary

- Lab roots and public roots have distinct mandatory names, profiles, lightwalletd endpoints, and block 1 checks. The public endpoint is fixed to `https://zsa.methyl.cc`; a lab profile accepts only loopback. The protected `/private/tmp/zwa4e-phase4e-b` path and traversal spellings fail before opening it. The Phase 4E repository paths were not modified.
- Issuer, recipient and lab faucet have separate newly generated `rlz` wallet databases under an owner-only `0700` lab root, with owner-only files. No protected seed, private key, wallet database, witness, or Phase 4E identity was imported or copied. Only the in-process recipient viewing key was used to test decryption.
- Website lab data contains only the client's privacy-checked public receipt fields: chain identity, txids, blocks, public issuance identity and amount, fee, and a private-wallet status. The transfer amount, addresses, viewing keys, spending keys, witnesses, and wallet balances are not included. Lab cards say **ISOLATED LAB · NOT PUBLIC TESTNET** and do not imply observers can see shielded amounts.
- Lab submission used only loopback lightwalletd, except the deliberately corrupted transaction sent to **loopback** Zebra RPC for a negative consensus test. There was no public RPC broadcast, public faucet request, public wallet root, mainnet transaction, deployment, or publication.
- A single operator mines the experimental public network. A reset, service substitution, reorg, stale lightwalletd, or source-pin divergence remains possible. Confirmations are observations, not finality. Neither this lab nor a future issuance proves ZWA Groth16 authorization, atomic swap settlement, or protocol-level fee enforcement.

## Future public transaction plan and owner-operated commands

**Separate approval gate:** the owner must explicitly approve any public faucet use and, later, each exact public txid broadcast. These commands are a reviewable future runbook; none has been run in Phase 2. Recheck the public block 1 pin and live service before creating a root. The previous read-only faucet observation was 0.01 tZEC (1,000,000 zat) per claim, but availability and Orchard-address support must be checked by the owner. The lab observed 35,000 zat of issuance plus transfer fees, leaving 965,000 zat from a direct 1,000,000-zat Orchard funding note; future fees and action counts must be checked again from each prepared plan. If the faucet funds only transparent addresses, this client has no public shielding command and the public plan is blocked until a separately reviewed shield-only path exists.

After **separate faucet authorization**, the owner can create new public identities and request testnet funds through the faucet's human interface. The address printed here is private to the owner and must never be pasted into receipts or the website:

```sh
cd /Users/mahesh/Desktop/Colosseum/ZWA/ZWA-Brand-Integration
ZWA_TOOL=/private/tmp/zwa-pub-build/tool-target/release/zwa-zsa-pub
ZWA_ROOT=/private/tmp/zwa-pub-public-owner1
"$ZWA_TOOL" --root "$ZWA_ROOT" init --profile public
"$ZWA_TOOL" --root "$ZWA_ROOT" wallet-init --role issuer
"$ZWA_TOOL" --root "$ZWA_ROOT" wallet-init --role recipient
"$ZWA_TOOL" --root "$ZWA_ROOT" identity
"$ZWA_TOOL" --root "$ZWA_ROOT" address --role issuer --kind orchard
# Owner requests faucet funds to that Orchard address only after separate approval.
"$ZWA_TOOL" --root "$ZWA_ROOT" sync --role issuer
"$ZWA_TOOL" --root "$ZWA_ROOT" balance --role issuer
"$ZWA_TOOL" --root "$ZWA_ROOT" prepare-issue --name ZWA-TESTNET-OWNER-1 --amount 1000000 > "$ZWA_ROOT/issue-plan-owner.json"
ISSUE_TXID=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["facts"]["txid"])' "$ZWA_ROOT/issue-plan-owner.json")
"$ZWA_TOOL" --root "$ZWA_ROOT" inspect --txid "$ISSUE_TXID"
```

The owner reviews the issue plan, raw-byte node check, live identity, fee, expiry, and exact txid. **Only after a new explicit broadcast authorization for that exact txid**, an interactive owner terminal can run:

```sh
ZWA_OWNER_BROADCAST_APPROVAL="$ISSUE_TXID" "$ZWA_TOOL" --root "$ZWA_ROOT" broadcast --txid "$ISSUE_TXID"
# At the prompt, the owner types exactly: BROADCAST <the displayed ISSUE_TXID>
"$ZWA_TOOL" --root "$ZWA_ROOT" verify --txid "$ISSUE_TXID"
"$ZWA_TOOL" --root "$ZWA_ROOT" balance --role issuer
```

After issuance inclusion and issuer scan, the owner may prepare a transfer. Preparation signs locally but does not send. A **second, separate approval for its exact txid** is required to submit it:

```sh
ASSET_BASE=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["asset_base"])' "$ZWA_ROOT/issue-plan-owner.json")
"$ZWA_TOOL" --root "$ZWA_ROOT" prepare-transfer --asset-base "$ASSET_BASE" --amount 250000 > "$ZWA_ROOT/transfer-plan-owner.json"
TRANSFER_TXID=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["facts"]["txid"])' "$ZWA_ROOT/transfer-plan-owner.json")
"$ZWA_TOOL" --root "$ZWA_ROOT" inspect --txid "$TRANSFER_TXID"
# Only after separate exact-txid authorization in a later owner review:
ZWA_OWNER_BROADCAST_APPROVAL="$TRANSFER_TXID" "$ZWA_TOOL" --root "$ZWA_ROOT" broadcast --txid "$TRANSFER_TXID"
# At the prompt, the owner types exactly: BROADCAST <the displayed TRANSFER_TXID>
"$ZWA_TOOL" --root "$ZWA_ROOT" verify --txid "$TRANSFER_TXID"
"$ZWA_TOOL" --root "$ZWA_ROOT" balance --role issuer
"$ZWA_TOOL" --root "$ZWA_ROOT" balance --role recipient
```

No exact public txid exists yet. Any prepared transaction can expire before approval and must be rebuilt; the tool refuses submission within three blocks of expiry. Before any public receipt is added to the website, compare returned bytes and txid with the public block entry, rerun both wallet scans, and have the owner review the receipt for private material.

## Review gate

**PASS** — isolated laboratory acceptance and privacy boundary. **BLOCKED pending separate owner authorization** — faucet use and all public broadcasts. The public network's current faucet behavior, funding path, miner/service continuity, and actual public transaction acceptance remain untested. Do not treat the lab blocks or the verification page's lab cards as public testnet evidence.
