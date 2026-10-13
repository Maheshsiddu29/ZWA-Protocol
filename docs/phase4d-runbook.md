# Phase 4D local exchange runbook

All instruments here are **synthetic**, on disposable experimental ZSA regtest. These commands use the pinned Phase 4C.1 tx tool and development Groth16 key. The three retained runs in `exchange/recorded-runs.json` are historical scripted test evidence. Their chains have stopped, so their current finality is unknown.

## Requirements

- macOS, Docker, `sandbox-exec`, `sqlite3`, Python 3, Node.js, `rg`, and the pinned local tx tool at `/private/tmp/zwa4c1-tx-tool` commit `16052323904be8efbb9e0f92d059fa57c9198f8a`.
- The pinned Zebra image `sha256:540a3429ef14ff3c575258255e4310951606d16c86ec5e1c37e285ea95178185`.
- Existing trusted Sapling parameters under `/Users/mahesh/Desktop/Colosseum/ZWA/zsa-feasibility/.local/share/ZcashParams`. The launchers verify their SHA-256 values. Do not run the quarantined parameter fetcher.
- Fresh `/private/tmp/zwa4d-*` root and unique container and loopback ports for each run. The root holds private seeds, wallet databases, signed authorization material, and RPC transaction bytes. Keep it outside Git.

## Execute one selected asset

From this repository root, select exactly one of `zwa-gold-reference`, `zwa-treasury-style`, or `zwa-credit-participation`. The script issues **all three** independent ZSAs on one fresh chain, checks each issuance with RPC and seller wallet inventory, then purchases the selected one. Each run has a new chain and new AssetBases.

```sh
PARAMS_DATA_HOME=/Users/mahesh/Desktop/Colosseum/ZWA/zsa-feasibility/.local/share \
ROOT=/private/tmp/zwa4d-my-gold-run PORT=18320 API_PORT=18820 \
CONTAINER=zwa-phase4d-my-gold INSTRUMENT_ID=zwa-gold-reference \
SCRIPTED_TEST_MODE=1 OBSERVE_SECS=120 \
./checkout/operator/phase4d-scripted-validation.sh
```

This scripted path automatically authorizes through the pinned test signer flag. It proves integration, **not human approval**. `ROOT/exchange/instrument-registry.json` contains public issuance and inventory observations; `ROOT/exchange/api-depth3.json`, `api-receipt.json`, `public-scans.json`, and `independent-block.json` contain the public validation evidence. The launcher tests exact Groth16 authorization and separate buyer/seller signer responses before submission, then checks the txid against the current block and wallet balance changes. It stops its node and API after the observation window.

For a human-approved run, use a separate fresh root and `./checkout/operator/phase4d-human-demo.sh` with the same environment except `SCRIPTED_TEST_MODE`. It requires a real interactive terminal. The buyer and seller must each type the displayed `APPROVE <digest>` in their own wallet prompt. The default is refusal. The browser has no signing endpoint. Do not type approvals on someone's behalf or relabel a scripted run as human-approved.

## View the live exchange

While the script holds the node and checkout API open, start the website in another terminal:

```sh
cd website
npm ci
npm run lint
npm run build
ZWA_CHECKOUT_URL=http://127.0.0.1:18820 \
ZWA_ACTIVE_INSTRUMENT_ID=zwa-gold-reference \
ZWA_EXCHANGE_REGISTRY_PATH=/private/tmp/zwa4d-my-gold-run/exchange/instrument-registry.json \
./node_modules/.bin/next start -H 127.0.0.1 -p 3216
```

Open `http://127.0.0.1:3216/exchange`, `/exchange/assets/zwa-gold-reference`, and `/exchange/judge`. The exchange accepts only the local operator manifest when the checkout API says the current node is available. It additionally requires the selected catalog ID, manifest AssetBase, live quote AssetBase, and all three exact authorization statuses to agree before showing a live checkout. The other two assets are labeled experimental issued. If the node stops, all live availability disappears; recorded runs remain historical on judge mode.

For a fresh indicative gold reference, first verify the full XAU/USD feed ID against Pyth's official live feed metadata. Set `PYTH_XAU_USD_FEED_ID_VERIFIED` to that exact 64-character ID and set `PYTH_API_KEY` in the website server environment. The server requests the pinned Pyth Core XAU/USD feed, checks its ID, integer exponent, confidence, and observation age, and displays its timestamp. Without both settings or with stale/invalid data, the price reads unavailable. This HTTPS API path **does not verify a signed Wormhole update** and **does not set the native ZEC execution payment**. The seller wallet approves the exact ZEC amount in the quote.

## Provision local participant access

Run `python3 website/scripts/provision-local-roles.py /private/tmp/zwa4d-roles-my-demo` and enter four distinct passwords interactively. Source the resulting mode-0600 file only in the local website server process. It exports `ZWA_ROLE_ACCOUNTS` and `ZWA_SESSION_SECRET`. Portal login at `/exchange/connect` is separate from wallet pairing and signing. No browser form can mint a credential or change role. Set `ZWA_AUDITOR_ALLOWED_TXIDS` to a comma-separated list of exact recorded txids to authorize an auditor's historical receipt view; omitted means no receipts. Do not put the role file in the repository.

## Judge path and negative case

1. Browse `/exchange/assets`; inspect the gold asset's rights, issuer, status, and available XAU reference.
2. Prepare and inspect the exact native ZEC quote in `/demo#checkout` and the buyer/seller terminal reviews.
3. Complete a scripted or genuinely human-approved local run as labeled; inspect the current receipt, buyer asset scan, seller ZEC scan, and confirmation depth.
4. Visit `/exchange/judge` for the three separately recorded asset-class transactions. They are historical; they do not authorize the current run.
5. Demonstrate a rejection using `node checkout/authorization/test_adversarial.js <fresh-phase4c1-root> <unrelated-terms.json>` against disposable current terms. Its cases include wrong AssetBase, altered price and receiver, nonce replay, stale/forged roots, invalid Groth16 proof, and unrelated research proof. The test script accepts only `/private/tmp/zwa4c1-*` paths; a temporary local symlink to a fresh Phase 4D root can be used, then removed.

Never present the rooted-lineage research fixture as proof of the checkout output. The native-ZEC checkout still has unsupported purchased-note Halo2 lineage and known Action Group expiry limitations. There is no native mainnet ZSA settlement, reserve audit, legal title, regulated investor credential, production browser wallet, or irreversible finality claim.
