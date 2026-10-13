# Phase 4B local demo operator runbook

This flow uses disposable regtest wallets and the pinned experimental Zebra image. It is an experimental **ungated** ZSA-for-ZEC settlement demonstration. The rooted-lineage verifier above the checkout on `/demo` uses a separate pinned research view. Do not describe that verdict as proof of this purchase.

## Prerequisites and safety checks

- Use macOS with `docker`, `sandbox-exec`, `sqlite3`, `curl`, `rg`, Python 3 and a current Node.js. Run the signer flow in a real interactive terminal.
- Use the isolated tx-tool checkout at `/private/tmp/zwa35c-tx-tool`, revision `3dd2a010fd557ef15a68915e42fec8289dc7d7d2`, with its existing executable `target/release/zcash_tx_tool`. The launcher checks the revision.
- The pinned Zebra image is `sha256:540a3429ef14ff3c575258255e4310951606d16c86ec5e1c37e285ea95178185`. The launcher checks it before creating wallets.
- Point `PARAMS_DATA_HOME` to an existing trusted XDG data directory containing readable `ZcashParams/sapling-spend.params` and `ZcashParams/sapling-output.params`. Both launchers check the pinned SHA-256 values before wallet creation and fail if either file is absent or changed. In this workspace the data home is `/Users/mahesh/Desktop/Colosseum/ZWA/zsa-feasibility/.local/share`. Do not run the quarantined `fetch-params.sh`.
- The chosen `ROOT` must be a **new** direct child of `/private/tmp` whose name begins `zwa4b-`. It will contain private seeds and wallet databases. Never add it to Git or share it.
- Confirm the default ports 18255 (Zebra), 18745 (checkout API), 3000 (verifier) and 3210 (website) are free. The signer API and Zebra bind loopback only.

## Start the site and independent verifier

From `ZWA-Brand-Integration/website`:

```sh
npm ci
npm run lint
npm run build
ZWA_CHECKOUT_URL=http://127.0.0.1:18748 ./node_modules/.bin/next start -H 127.0.0.1 -p 3212
```

In another terminal, from `ZWA-Brand-Integration/lineage`:

```sh
ZWA_CHAIN_VIEW=A7 ZWA_DEMO_ENABLED=true ./target/debug/zwa-lineage-api
```

Build the pinned verifier first using `docs/phase3-api.md` if the binary is absent. Check `http://127.0.0.1:3000/health/ready` and open `http://127.0.0.1:3212/demo`. The verifier panel can run its real A7 fixture, but its result is **independent research evidence**.

## Prepare and execute one fresh session

In a third real terminal from `ZWA-Brand-Integration/`:

```sh
PARAMS_DATA_HOME=/Users/mahesh/Desktop/Colosseum/ZWA/zsa-feasibility/.local/share \
ROOT=/private/tmp/zwa4b-owner-run-002 \
PORT=18258 API_PORT=18748 CONTAINER=zwa-phase4b-owner-002 \
./checkout/operator/phase4b-human-demo.sh
```

Choose a new `ROOT` name on every run. The launcher starts a clean pinned Zebra container; creates buyer, seller and sponsor wallets with private homes and sandbox profiles; mines 100 funding blocks; funds buyer native ZEC; has seller issue 100 units of one new ZSA; mines issuance; scans starting balances; and prepares the exact 7-ZSA-for-10,000-zatoshi quote. The sponsor fee is 5,000 zatoshis; matcher fee is zero; buyer debit is exactly 10,000 zatoshis. No browser action starts these steps.

The separate `checkout/operator/phase4b-scripted-validation.sh` requires `SCRIPTED_TEST_MODE=1` and uses the pinned signer's `--scripted-test-mode` acceptance flag. Its events and website view explicitly say `SCRIPTED_TEST_MODE`; they are never human approval evidence. The human launcher above has only `authorize-interactive` signer calls.

The launcher starts the read-only checkout API and waits for `availability.state: AVAILABLE`. The site then discovers `GET /v1/checkout/active` through its same-origin proxy. It displays the newly generated AssetBase and quote digest. Keep the terminal open.

When prompted, the buyer owner reads the signer review and types `APPROVE <exact displayed digest>` **inside the buyer wallet terminal**. Default, empty, EOF and any other input reject. The seller owner then makes an independent decision in the seller wallet terminal. Codex, a shell script, or a test harness must not type either phrase for a human-run claim. The browser has no signing endpoint and no wallet keys. The page shows `AWAITING_BUYER_APPROVAL` and `AWAITING_SELLER_APPROVAL` based on operator events; those events alone do not prove who typed.

After both signers authorize, the coordinator receives the two signed Action Groups through its local mode-0600 Unix socket, submits the assembled transaction, and mines inclusion. The launcher checks the transaction against independent `getblock` and `getrawtransaction` RPC responses, rescans both wallets, mines two more blocks, and reads the live API receipt. It publishes public wallet scan effects only when their asset ID, session digest, height and before/after changes reconcile with the verified quote and receipt. The site shows the actual txid, block hash, confirmation depth and observed scans. Depth three is a local policy threshold, not irreversible finality.

The launcher waits at `Press Enter` after success so the node and API remain live for judges. Use another terminal to inspect:

```sh
curl -fsS http://127.0.0.1:18748/v1/checkout/active
curl -fsS http://127.0.0.1:18748/v1/checkout/sessions/<actual-quote-digest>/receipt
```

The private run directory holds `exchange/api-depth3.json`, `exchange/api-receipt.json`, `exchange/public-scans.json`, `exchange/independent-block.json`, and `exchange/independent-transaction.json`. The latter transaction file may contain full transaction bytes and must stay local. The public scans and API views are observations from this one run, not permanent chain evidence after the node stops. Press Enter to stop the node and API. The site must return an unavailable state rather than replaying the old live success.

## Failure and recovery

- **Missing parameters or image:** the launcher stops before creating wallets. Point to already trusted local parameter files or restore the known pinned image separately. Never execute the quarantined parameter fetch script.
- **Buyer or seller does not grant approval:** the signer returns nonzero, the operator event says `*_APPROVAL_NOT_GRANTED`, and the launcher exits without intentionally submitting. Inspect private role logs. A disclosed signed group cannot be revoked merely by a local timeout or rejection after the other party signed.
- **Coordinator timeout, crash, RPC loss or uncertain result:** stop presenting the session as successful. Inspect the private coordinator and API logs, the session result, and the node. If any signed group was released, do not reuse the wallets or groups. Stop the disposable container and start a new `ROOT` and container name after understanding the failure.
- **Node lost after inclusion or reorganization:** the live API returns 503 or `INVALIDATED_BY_REORG`; do not use the old receipt as current settlement evidence.
- **Port collision:** choose unused `PORT`, `API_PORT`, and `CONTAINER` values for the operator launch; set `ZWA_CHECKOUT_URL` to the chosen loopback API origin before starting Next.js.
- **No active session:** the website may still show `recorded-phase35h` under the separate historical disclosure. It never silently switches this into the live panel.

The old Phase 3.5H transaction `aaeb15b837d5b44fc6aaf6d88b36ac9b802cd53e6cd2cf292ef8450db6826dfd` is historical only. Every successful new run must have a different newly observed txid.
