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
| `zebra` | pinned per platform: arm64 `sha256:540a3429…5185`, amd64 `sha256:2c3f3dde…32b9` | Experimental regtest chain | internal, never published | chain state |
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

Two Zebra images are pinned. The owner declares both were built from QED-it/zebra
`0aef55cea41b83f17610e6ea708e59995f9e739f`. The image IDs identify image
content, not source, and there is no reproducible-build evidence for the amd64
image. The local **linux/arm64** build is
`sha256:540a3429ef14ff3c575258255e4310951606d16c86ec5e1c37e285ea95178185` and
the **linux/amd64** build used on the AWS EC2 host is
`sha256:2c3f3dde0230f572911d70b7f74cc8f4587c1a139b0756d904e3b09eba2e32b9`.
`scripts/preflight.sh` refuses a tag that does not resolve to the pin for the
host's own platform (no emulation). See
[`docs/zebra-provenance-migration.md`](../docs/zebra-provenance-migration.md).

| Item | Size |
|---|---|
| VM | 4 vCPU, 8 GiB RAM, arm64 (e.g. AWS c7g.xlarge, Hetzner CAX31) or amd64 with the amd64 pin |
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
python3 -I scripts/zebra-runtime-attest.py verify --image "$ZWA_ZEBRA_IMAGE" --project zwa-demo
docker compose --profile setup run --rm bootstrap
docker compose up -d
```

On an amd64 host, add `-f compose.yaml -f compose.amd64.yaml` (the host's
AMD64 override, which must replace the arm64 zebra entrypoint) to **every**
`docker compose` command, and use `--docker-sudo` on the attestation tool
where Docker needs root.

The `verify` step runs on the host; no container has the Docker socket. It
checks that the running `zebra` container uses exactly the pinned image ID
for this platform. Repeat it after **every** Zebra start, restart, recreation
or image change: nothing inside the engine can see the container.
`bootstrap` records only the version-1 `image` label, so status reports
`provenance.evidence: LABEL_ONLY`. The explicit
`exchange.zebra_provenance record --expect-compose-project …` step in
[`docs/zebra-provenance-migration.md`](../docs/zebra-provenance-migration.md)
adds `POINT_IN_TIME_HOST_ATTESTATION`. That is evidence of one container at one
time, never of the image running now (`currentRuntimeVerified: false`).

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

For the Compose stack, set `ZWA_SITE_ADDRESS=localhost`,
`ZWA_PUBLIC_ORIGIN=https://localhost:8443`, `ZWA_HTTP_PORT=8088`, and
`ZWA_HTTPS_PORT=8443` in `deploy/.env`. Leave `ZWA_BIND_ADDRESS` unset (the
default is `127.0.0.1`). Caddy creates a local development CA in its named
`caddy-data` volume. Export **only its public root certificate**, verify its
fingerprint with the operator, and trust that certificate in the macOS login
keychain for a normal browser session:

```sh
cd deploy
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt /private/tmp/zwa-local-caddy-root.crt
openssl x509 -in /private/tmp/zwa-local-caddy-root.crt -noout -subject -fingerprint -sha256
security add-trusted-cert -r trustRoot -k "$HOME/Library/Keychains/login.keychain-db" /private/tmp/zwa-local-caddy-root.crt
curl --fail --show-error https://localhost:8443/ -o /dev/null
```

The macOS keychain may require the operator to authorize the trust change.
Never export or trust Caddy's CA **private key**. Never bypass a browser
certificate warning. Verify that a wrong hostname fails validation before
relying on this origin. The trust is local to this developer machine; a future
public deployment needs its own approved domain and public certificate.

Run the HTTP acceptance over validated TLS with
`--base https://localhost:8443 --ca-file /private/tmp/zwa-local-caddy-root.crt`.
Pass `--rpc "exec:docker compose exec -T portal python3 -c …"`, because the
node is not reachable from the host.

The focused two-context browser security test is
`node tests/acceptance/browser_security.cjs` from the repository root. It
requires the Playwright package and an installed Chrome browser; set
`ZWA_PLAYWRIGHT_PACKAGE` to a separate Playwright installation if needed, and
set `NODE_EXTRA_CA_CERTS=/private/tmp/zwa-local-caddy-root.crt` so Playwright's
Node API client trusts the same CA as Chrome.
It launches Chrome with normal certificate validation, provisions two new
disposable wallets, tests session boundaries, and retires both wallets. It
does not confirm a purchase.

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
- **Backup and restore:** stop all writers, including `zebra`, `order-service`,
  `demo-signer`, `portal`, `web`, and `caddy`. Archive `zwa-root`,
  `zebra-state`, and `zwa-params` from that stopped point; include `caddy-data`
  and `caddy-config` if preserving this same local HTTPS identity. Write the
  archives to an owner-only directory on an encrypted disk. Restore the paired
  state into **new volumes in an isolated Compose project**, with no ports
  published, before any restart. Do not restore onto a live stack or combine
  archives from different points in time. Run
  `docker compose exec -T portal python3 - < scripts/m10-state-report.py`
  before the stop and after the isolated restore; compare genesis, order and
  receipt states, buyer and dealer balances, offers, and ledger integrity.
  A restored Zebra may discard its latest 99 blocks. Funding is now marked
  READY only below a durable chain anchor; recovery revokes a wallet whose
  funding block disappeared and refreshes surviving wallet scans. A pending
  quote may expire during the drill without creating a settlement. Keep
  recovery archives private because `zwa-root` contains disposable wallet
  keys. The M10 drill and observed rollback are in
  `docs/m10-hosted-https-security-review.md`.
- **Reset the demo:** `docker compose down`, remove both volumes, then run the
  bootstrap again. This never affects the owner's protected Phase 4E chain,
  which is not part of this deployment.

## Cloud runbook prepared for owner review

Use a **fresh** host whose platform matches a pinned Zebra image (arm64 or
amd64) and fresh named volumes. Never transfer the
protected Phase 4E chain or any existing wallet to that host. Before executing
the commands above, the owner chooses the provider, encrypted disk, budget,
domain, DNS records, firewall rules, TLS method, backup destination, and access
policy. Keep `ZWA_BIND_ADDRESS=127.0.0.1` until those decisions are approved.
For public service, expose only TCP 80/443 to Caddy, leave Docker's internal
network private, and deny direct access to 18232, 8790, 3000, and the signer.
Check `docker compose config`, `./scripts/preflight.sh`, image digests,
`docker compose ps`, portal `/readyz`, browser TLS, a disposable purchase, its
chain block and receipt, and a paired-volume restore on the new host.

Schedule encrypted backups only after a tested stopped-service snapshot or a
validated coordinated snapshot procedure. Keep a separate rollback copy of
the prior images and paired volumes. On failure, stop Caddy first, stop the
writers, restore that matched copy in an isolated project, verify the
`m10-state-report.py` output, and only then resume service. For shutdown,
pause sales, allow or explicitly cancel pending quotes, stop the web edge,
then stop the engine and Zebra. The current 8 GiB VM target is a planning
baseline: Compose memory ceilings total 9.5 GiB, so a loaded 8 GiB VM needs
a real peak-memory trial or a larger host before public launch.

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
