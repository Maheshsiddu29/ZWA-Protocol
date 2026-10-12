# Phase 4E-A persistent regtest runbook

This is an **experimental, scripted test-mode** three-purchase run on one pinned Zebra regtest chain. It does not create legally backed RWAs, provide human wallet approvals, or make the exchange website a complete buyer checkout. Keep every private session root outside Git.

## Requirements

- macOS with Docker, `sandbox-exec`, `sqlite3`, Python 3, Node.js, and `rg`.
- Pinned tx tool at `/private/tmp/zwa4c1-tx-tool`, HEAD `16052323904be8efbb9e0f92d059fa57c9198f8a`, with release binary already built.
- Pinned Zebra image `sha256:540a3429ef14ff3c575258255e4310951606d16c86ec5e1c37e285ea95178185` already available locally.
- Trusted Sapling parameters under `/Users/mahesh/Desktop/Colosseum/ZWA/zsa-feasibility/.local/share/ZcashParams`. The validation script checks both SHA-256 hashes. Never use the quarantined parameter fetcher.
- A new direct `/private/tmp/zwa4e-*` root, unique container name, and free loopback ports. The root will contain wallet seeds, databases, private proof material, and chain state. It must remain owner-only.

## First start and three purchases

From this repository root, choose an unused root and ports. Do not reuse a Phase 4D disposable root.

```sh
python3 -m exchange.persistent_chain start \
  --root /private/tmp/zwa4e-my-run \
  --container zwa-phase4e-my-run --port 18320

PARAMS_DATA_HOME=/Users/mahesh/Desktop/Colosseum/ZWA/zsa-feasibility/.local/share \
ROOT=/private/tmp/zwa4e-my-run PORT=18320 API_PORT=18820 \
CONTAINER=zwa-phase4e-my-run SCRIPTED_TEST_MODE=1 \
./checkout/operator/phase4e-a-scripted-validation.sh
```

The script refuses existing buyer, seller, or coordinator profiles and a nonempty chain at the initial issuance step. It creates three different real experimental ZSAs, observes issuance and seller inventory, funds the buyer with actual shielded regtest ZEC, and executes three sequential purchases with the same buyer and seller seeds. Each purchase uses the unchanged Phase 4C.1 Groth16 gate, independent buyer and seller **test signers**, and real pinned Zebra inclusion. It checks block membership, wallet deltas, and confirmation depth. The three amounts are operator-selected fixed native-ZEC test terms; **they are not derived from a live XAU/USD, NAV, or ZEC/USD source**.

The pinned Zebra image drops recent non-finalized blocks at shutdown, even with an on-disk state database. The script mines 1,100 additional empty regtest blocks after the trades so the purchase anchor survives a node stop. It then stops and restarts the same container and rescans both wallets. A restart that loses the anchor is labeled `CHAIN_RESET` and refuses stale portfolio promotion. The validated chain stays running after the script returns.

Private evidence is in `ROOT/exchange/`: `instrument-registry.json`, per-instrument issuance and chain responses, `trades/<instrumentId>/` with exact quote/proof/receipt/scan evidence, `portfolio.json`, and `portfolio-after-restart.json`. The portfolio files contain private balance data and are **not** public catalog or API fixtures. The `chain-identity.json` file contains pinned image/config identity, genesis hash, and the last explicitly checked block anchor.

## Read-only browser portfolio and seller inventory

Provision four distinct local portal accounts with `python3 website/scripts/provision-local-roles.py /private/tmp/zwa4d-roles-my-run`, then source its mode-0600 output only in the website server shell. For the stock provisioner, the buyer and seller account IDs are `buyer` and `seller`.

```sh
cd website
source /private/tmp/zwa4d-roles-my-run
export ZWA_PRIVATE_EXCHANGE_ROOT=/private/tmp/zwa4e-my-run
export ZWA_CHAIN_RPC_PORT=18320
export ZWA_PORTFOLIO_BUYER_ID=buyer
export ZWA_PORTFOLIO_SELLER_ID=seller
npm run build
./node_modules/.bin/next start -H 127.0.0.1 -p 3216
```

Open `http://127.0.0.1:3216/exchange/connect` in separate browser sessions. Log in as the provisioned buyer for `/exchange/portfolio` and as the seller for `/seller`. These pages read only the newest private combined wallet scan, and only after the live pinned Zebra RPC confirms the same genesis, saved block anchor, scan tip, and every listed transaction's block membership. If the node stops, the chain changes, the scan is stale, or the configured portal account does not match, no private holdings are rendered. The buyer page receives only buyer balances; the seller page receives only seller inventory and proceeds. Auditor and unrelated accounts cannot open either role page. This is portal authentication and observation, **not wallet pairing or signing**.

## Check state, stop, restart, and rescan later

```sh
python3 -m exchange.persistent_chain status \
  --root /private/tmp/zwa4e-my-run \
  --container zwa-phase4e-my-run --port 18320

python3 -m exchange.persistent_chain stop \
  --root /private/tmp/zwa4e-my-run \
  --container zwa-phase4e-my-run --port 18320

python3 -m exchange.persistent_chain start \
  --root /private/tmp/zwa4e-my-run \
  --container zwa-phase4e-my-run --port 18320

PARAMS_DATA_HOME=/Users/mahesh/Desktop/Colosseum/ZWA/zsa-feasibility/.local/share \
ROOT=/private/tmp/zwa4e-my-run PORT=18320 CONTAINER=zwa-phase4e-my-run \
./checkout/operator/phase4e-a-rescan.sh
```

`status` returns `CHAIN_ACTIVE`, `CHAIN_RESTARTED`, `CHAIN_STOPPED`, or `CHAIN_RESET`. A stopped node cannot attest current holdings. A reset is sticky in the identity file; start refuses it. The rescan command requires an active anchor-verified node and produces a new owner-only `ROOT/exchange/rescans/scan.*/portfolio.json` from fresh buyer and seller wallet scans. It does not sign or submit anything. Do not display prior portfolio files as current if the node is stopped, the anchor changed, or the scan height is above the live tip.

## Approval and scope

This path uses `SCRIPTED_TEST_MODE=1` and must only be described as automated regression evidence. The owner has not approved these purchases in human mode. The Phase 4D human signer remains a separate, one-purchase terminal path. No browser may sign through this script, and neither wallet seed nor private proof witness belongs in a web response or committed artifact.

The complete Phase 4E product still needs distinct issuer wallet issuance, a durable order service, authenticated quote-to-price commitment, local wallet pairing and human approval UI, four-role resource isolation, private rooms, all-category acceptance, and browser-tested flows. See `phase4e-a-report.md` for the observed run and limitations.
