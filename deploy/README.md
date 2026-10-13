# Hosted judge demo — deployment

**Status: prepared and rehearsed locally. Not deployed.** Provisioning a VM,
DNS, opening the HTTPS endpoint and uploading anything to a cloud host all
need explicit owner approval.

## What runs

```
internet ──443──▶ caddy ──▶ web (Next.js) ──▶ portal API ─┐
                    edge network   │            order-service ├─▶ zebra (pinned regtest)
                                   │            demo-signer  ─┘     internal network only
                                   └─ no volumes                    shared volume: zwa-root
```

| Service | Image | Role | Network | Writes |
|---|---|---|---|---|
| `caddy` | `caddy:2.8.4-alpine` | TLS, HSTS, body limit, real client address | edge (ports 80/443) | certificates |
| `web` | `zwa-web` | Pages and HTTP API; signs session cookies; CSRF, Origin and rate checks | edge + internal | nothing (read-only rootfs) |
| `portal` | `zwa-engine` | Fixed-action bridge (`exchange.portal_server`), bearer-token protected | internal | ledger |
| `order-service` | `zwa-engine` | Quotes, Groth16 proof, coordinator, settlement, wallet provisioning, durability | internal | ledger, evidence, wallets |
| `demo-signer` | `zwa-engine` | Restricted policy signer for judge wallets and the dealer | internal | approval attestations |
| `zebra` | pinned `sha256:540a3429…5185` | Experimental regtest chain | internal, never published | chain state |
| `params` | `zwa-engine` | One-shot: copy and checksum the Sapling parameters into a volume | internal | parameters, once |
| `bootstrap` | `zwa-engine` | One-shot: adopt a fresh chain, create dealer and sponsor, issue, list | internal | everything, once |

Security boundaries:

- **No admin RPC exposure.** Zebra's RPC is reachable only on the `internal`
  network, which has no route to the internet. Only `caddy` publishes ports.
- **Web holds no wallet data.** It mounts no volume and reaches the engine only
  through `POST /v1/portal` with a shared token. Actor, role and session come
  from its signed cookie, never from the request body.
- **Per-wallet isolation.** Every wallet, dealer and coordinator tool run is
  confined by Linux Landlock (`exchange/landlock_exec.py`) to its own home. The
  engine refuses to start if Landlock is unavailable. On macOS the same
  profiles are enforced with `sandbox-exec`.
- **No arbitrary signing.** The signer builds Action Groups only from
  structured terms whose hash the quote recorded. The demo signer adds the
  browser confirmation, limits, recipients, fee and tip checks
  (`exchange/demo_signer.py`).
- **Hardened containers.** Containers run as uid 10001 (except Zebra and
  Caddy), drop all capabilities, use `no-new-privileges` and a read-only
  rootfs, and have memory and CPU limits.
- **Bounded demo.** Limits are set by the v2 `service-config.json` written at
  bootstrap: 40 live wallets, 60 sign-ups per hour, 3 per client per hour, 5
  orders per wallet, 5 units and 0.01 ZEC per order, and 60 sponsored
  settlements per hour.
- **Blocks are mined only while no quote is live.** Funding, sponsor top-up,
  durability and the expiry of uncertain orders all wait for that.

Wallet keys at rest: wallet homes are mode 0700 inside the `zwa-root` volume,
on the host's disk. Use an encrypted disk (cloud provider volume encryption
or LUKS) for the volume. The demo keys are disposable regtest keys. They are
deleted when a session retires.

## Resource estimate

The pinned Zebra image is a local **linux/arm64** build. Use an arm64 VM, or
rebuild Zebra from its pinned source for amd64.

| Item | Size |
|---|---|
| VM | 4 vCPU, 8 GiB RAM, arm64 (e.g. AWS c7g.xlarge, Hetzner CAX31) |
| Disk | 60 GiB encrypted SSD: images ≈ 10 GiB (Zebra 8.5 GiB), chain grows ≈ 110 blocks per settled purchase |
| Throughput | One settlement at a time (single dealer wallet). About 35 s per purchase; wallet provisioning about 6 s |
| Rough cost | US$ 30–130 per month depending on provider |

Wallet scans replay the chain from height 1. After many thousands of
purchases, rebuild the demo with a fresh chain (`bootstrap` on new volumes).

## Exact commands (owner review)

Build, on a machine that has the pinned tool checkout and the Zebra image:

```sh
./deploy/scripts/prepare-build-context.sh          # prints TX_TOOL_SHA256=...
docker build -f deploy/engine/Dockerfile --build-arg TX_TOOL_SHA256=<value> -t zwa-engine:local .
docker build -f deploy/web/Dockerfile -t zwa-web:local .
docker save zwa-engine:local zwa-web:local qedit/zebra-zsa-swap-pinned:latest | gzip > zwa-images.tar.gz
```

On the VM (Docker Engine 25+ with Compose v2, Linux kernel with Landlock):

```sh
gunzip -c zwa-images.tar.gz | docker load
mkdir -p /srv/zwa/params/ZcashParams        # copy sapling-spend.params and sapling-output.params; preflight checks hashes
cd deploy
cp env-example/compose.env .env              # edit: domain, origin, params dir, portal token
mkdir -m 700 env
python3 ../website/scripts/provision-local-roles.py env/web.env dealer:seller
./scripts/preflight.sh
docker compose --profile setup run --rm params   # copies and checksums the parameters into a volume
docker compose up -d zebra
docker compose --profile setup run --rm bootstrap
docker compose up -d
```

The domain's DNS A/AAAA record must point at the VM before Caddy can obtain
a certificate. The Compose example binds Caddy to loopback for safe local
rehearsal. After owner approval for public exposure, set
`ZWA_BIND_ADDRESS=0.0.0.0` in `deploy/.env` and restrict the VM firewall to
ports 80 and 443 before `docker compose up -d`.

## Local rehearsal on the macOS developer machine

Uses `sandbox-exec` isolation and a separate disposable root. It never
touches `/private/tmp/zwa4e-phase4e-b`.

```sh
ROOT=/private/tmp/zwa4e-judge-rehearsal PORT=18350 CONTAINER=zwa-judge-rehearsal \
PARAMS_DATA_HOME=<dir containing ZcashParams> ./checkout/operator/judge-demo-setup.sh
PYTHONPATH=$PWD python3 -m exchange.order_service serve --root $ROOT &
PYTHONPATH=$PWD python3 -m exchange.demo_signer serve --root $ROOT &
python3 website/scripts/provision-local-roles.py /private/tmp/zwa-web-rehearsal.env dealer:seller
cd website && npm run build && set -a && . /private/tmp/zwa-web-rehearsal.env && set +a && \
ZWA_DEMO_MODE=1 ZWA_PRIVATE_EXCHANGE_ROOT=$ROOT ZWA_CHAIN_RPC_PORT=$PORT ZWA_PORTFOLIO_SELLER_ID=dealer \
  ./node_modules/.bin/next start -H 127.0.0.1 -p 3311
```

Acceptance (real chain, proofs and signers; about 10 minutes):

```sh
ZWA_ACCEPT_DEALER_PASSWORD=... python3 tests/acceptance/judge_demo_acceptance.py \
  --base http://127.0.0.1:3311 --rpc http://127.0.0.1:$PORT
```

For the Compose stack, use `--base https://localhost:8443 --insecure` with
`ZWA_SITE_ADDRESS=localhost` and `ZWA_PUBLIC_ORIGIN=https://localhost:8443`.
Pass `--rpc "exec:docker compose exec -T portal python3 -c …"`, because the
node is not reachable from the host.

## Operations

- **Health:** `docker compose ps`. The portal answers `GET /healthz`
  (liveness) and `GET /readyz` (configuration, ledger, chain identity) on the
  internal network.
- **Logs:** `docker compose logs -f order-service demo-signer portal`. Logs are
  structured JSON events with order IDs and txids. No keys, seeds, cookies,
  tokens or proof witnesses are logged.
- **Restarts:** every long-running service has `restart: unless-stopped`.
  The order service resumes each order from the durable ledger. An order
  whose coordinator stopped after both signatures becomes `UNCERTAIN`. The
  demo releases it only when the chain proves it never executed.
- **Pause sales:** sign in at `/sign-in` as the dealer and choose *Pause
  sales*.
- **Backup:** stop `order-service` and `demo-signer`, then
  `docker run --rm -v zwa-demo_zwa-root:/v:ro -v "$PWD":/b alpine tar czf /b/zwa-root-$(date +%F).tgz -C /v .`
  and the same for `zwa-demo_zebra-state`. Restore both volumes together.
  Chain identity is checked against the restored state on start.
- **Reset the demo:** `docker compose down`, remove both volumes, then run the
  bootstrap again. This never affects the owner's protected Phase 4E chain,
  which is not part of this deployment.

## Portal API (internal)

`POST /v1/portal`, header `Authorization: Bearer $ZWA_PORTAL_TOKEN`, body
`{"action", "actor", "role", "sessionHash", "payload"}`. Body ≤ 8 KiB.
Response `{"ok": true, "data": ...}` or `{"ok": false, "error": "..."}`.
The actions are the fixed set in `exchange/portal.py`. Anonymous actions are
limited to `demo-start`, `public-market` and `verify-tx`.

## Website HTTP API (public)

| Method and path | Who | Purpose |
|---|---|---|
| `POST /api/demo/session` | anyone | Start a judge session (rate-limited) |
| `POST /api/demo/end` | judge | End session; wallet is retired |
| `GET /api/exchange/market` | anyone | Listings, prices, scanned inventory |
| `GET /api/exchange/wallet` | judge, dealer | Wallet or dealer desk |
| `POST /api/exchange/orders` | judge | `{offerId, quantity, idempotencyKey}` |
| `GET /api/exchange/orders/{id}` | order party | Order status and receipt |
| `POST /api/exchange/orders/{id}/confirm` | judge | `{quoteDigest}` of the shown quote |
| `POST /api/exchange/orders/{id}/cancel` | judge | Before signing only |
| `POST /api/exchange/offers/{id}/price` | dealer | `{priceZatPerUnit}` |
| `POST /api/exchange/dealer-policy` | dealer | `{policy: "ACTIVE" \| "PAUSED"}` |
| `POST /api/exchange/login`, `/logout` | dealer | Form sign-in |

Every `POST` except sign-in and session start requires the session's
`x-zwa-csrf` token, a matching `Origin` and a body of at most 2 KiB.
