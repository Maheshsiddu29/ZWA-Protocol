# Phase 4E-C runbook — browser-initiated, human-wallet-approved purchase

This runs one browser-initiated gold-reference purchase on the protected
persistent experimental chain (`/private/tmp/zwa4e-phase4e-b`, container
`zwa-phase4e-b`, loopback RPC `18321`). The two wallet approvals must be typed
by the owner in the wallet companions' own terminals. **No agent, script or
pipe may supply them.** Automation typing `APPROVE …` would not be human
approval.

The price is a **seller-fixed experimental price** (1,000 zatoshis per unit,
classification `SELLER_FIXED_EXPERIMENTAL`). It is not an oracle, NAV or gold
market price. Issuer and credential roots are disposable test authorities.

## What is already prepared

- Protected ledger migrated to schema 3 with every prior row and event kept.
  The abandoned pre-approval reservation `f67b3ed74d74db8f3b6f6c74f3fff106`
  was cancelled through the ledger after confirming it had no quote, proof,
  signed group, submission or execution.
- `exchange/service-config.json` in the protected root: `HUMAN_APPROVED` mode.
  The configuration loader refuses `SCRIPTED_TEST_MODE` for this root.
- Pre-change checkpoint: `/private/tmp/zwa4e-phase4e-b-pre-4e-c-backup-20261012`
  (SQLite backup API for all four databases; live chain state not copied).
- Production website build at the current commit.

## Owner steps

Use five terminals, all starting in
`/Users/mahesh/Desktop/Colosseum/ZWA/ZWA-Brand-Integration`.

**1. Portal credentials (once).** Choose a buyer and a seller password of 16+ characters.

```sh
python3 website/scripts/provision-local-roles.py /private/tmp/zwa4d-roles-phase4e-c-human
```

**2. Order execution service.** It never signs; it quotes, proves, starts the
coordinator, and records evidence.

```sh
PYTHONPATH=$PWD python3 -m exchange.order_service serve --root /private/tmp/zwa4e-phase4e-b
```

**3. Portal.**

```sh
cd website
set -a; source /private/tmp/zwa4d-roles-phase4e-c-human; set +a
ZWA_PRIVATE_EXCHANGE_ROOT=/private/tmp/zwa4e-phase4e-b ZWA_CHAIN_RPC_PORT=18321 \
ZWA_PORTFOLIO_BUYER_ID=buyer ZWA_PORTFOLIO_SELLER_ID=seller \
./node_modules/.bin/next start -H 127.0.0.1 -p 3216
```

**4. Seller wallet companion** (its own interactive terminal):

```sh
PYTHONPATH=$PWD python3 -m exchange.wallet_companion --root /private/tmp/zwa4e-phase4e-b --role seller
```

**5. Buyer wallet companion** (its own interactive terminal):

```sh
PYTHONPATH=$PWD python3 -m exchange.wallet_companion --root /private/tmp/zwa4e-phase4e-b --role buyer
```

Each companion shows a one-time pairing code that is valid for 5 minutes.

## Browser journey

Use two separate browser sessions so the cookies don't collide: the seller at
`http://localhost:3216` and the buyer at `http://127.0.0.1:3216` (or two browser
profiles).

Seller:
1. Open `/sign-in` and sign in as `seller`.
2. Open **Wallet**, enter the seller companion's code, then type `PAIR` in the
   seller companion terminal.
3. Open **Seller**, enter `1000` for ZWA Gold Reference Unit, and choose **Save price**.

Buyer:
1. Open `/sign-in` and sign in as `buyer`.
2. Open **Wallet**, enter the buyer companion's code, then type `PAIR` in the
   buyer companion terminal.
3. Open **Exchange → ZWA Gold Reference Unit**, keep quantity 1, and choose **Get exact quote**.
4. Review the exact quote (1,000 zatoshis; the network fee is paid by the test
   sponsor), tick **I confirm these exact terms**, then choose **Confirm purchase**.
5. Wait for "Approve in your wallet companion" on the order page. The Groth16 proof has already
   been generated and verified at this point.

Wallet approvals (owner only, within the coordinator's 300-second window):
1. **Buyer terminal:** read the signer's review line. Check that the terms hash
   begins with the digest prefix shown in the browser and that the effects are
   `+1` gold unit and `native:-1000`. Then type the exact
   `APPROVE <64-hex digest>` line the signer requests. Anything else rejects.
2. **Seller terminal:** the request appears after the buyer approves. Check
   the effects `-1` gold unit and `native:+1000`, then type the requested
   `APPROVE <digest>` line.

The service then records submission, block inclusion and independent wallet
scans, and writes the receipt. Next it mines 110 empty blocks so the accepted
block survives a pinned-Zebra restart. That is a node policy, not consensus
finality. Finally it rescans both wallets. The buyer portfolio should then
show 9 gold-reference units, and the seller desk should show 91 units and
33,000 zatoshis in cumulative proceeds.

## Failure behaviour

- Rejecting, mistyping, or letting the window lapse releases no signed group
  from that wallet. The order ends `EXECUTION_FAILED` and the reservation is
  released. If the buyer approved and the seller rejected, the buyer's group
  existed only in the stopped coordinator's memory and was never submitted.
- If a companion is not connected, the coordinator is never started. The
  buyer can cancel before any wallet approval.
- If the coordinator stops after both approvals without an accepted receipt,
  the order becomes `UNCERTAIN`. The reservation and wallet lock stay in place
  for operator review.
- If the chain is stopped or reset, every order pauses and no holdings are
  shown.

Stop the processes with Ctrl-C when done. Ctrl-C does not stop the protected
Zebra container.
