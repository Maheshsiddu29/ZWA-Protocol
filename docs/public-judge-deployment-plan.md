# Public judge demo: readiness, deployment and rollback plan

**Status: BLOCKED on owner decisions, not on code.** Nothing here has been run on the
AWS host. Every step marked **(owner)** needs explicit approval first. The plan adds no
infrastructure and no paid service.

Audit date: 2026-10-15. Reviewed frontend: `80abbd8` (branch
`integration/judge-frontend-purchase`). Repeatable real acceptance test: `7885aea`,
`64db924`.

## 1. Evidence so far

| What | Result |
|---|---|
| Real browser purchase, isolated local project `zwa-judge-e2e` (macOS, arm64) | **PASS.** 1 credit unit for 850 zat; txid `befbd7472995137b6cc8d243ccaf4bf3ac957dcbc40ba8e37a81e7a19ceadc7d`, block 436, five Groth16 verdicts VERIFIED, both custodial policy signers, reconciled, durable after 110 blocks, result unchanged after reload. Real Chrome, real portal, order service, demo signer, tx-tool and pinned Zebra. |
| Repository inspector against that project | Preflight 29/29, verify 21/21 (retrospective: the before-purchase facts came from a ledger probe taken just before the purchase). |
| Inspector negative tests | 15/15: unpinned Zebra, test-double processes, a stub transaction, a changed verifier, tx-tool or params, a published internal port, an unresolved order, missing durability, a skipped state, bad reconciliation, a duplicate order and a non-main-chain block each fail. |
| Read-only preflight of AWS `zwa-demo` | 29/29: pinned amd64 Zebra `2c3f3dde…32b9`, real engine services on one image, pinned tx-tool, Sapling parameters, verifier byte-identical to this repository, regtest genesis, ledger integrity ok, no unresolved order, no reservation. Tip 336, one earlier settlement. |

## 2. Existing host (read-only audit)

| Item | Finding |
|---|---|
| Host | EC2 instance `<host-ip>`, Ubuntu 24.04, x86_64, 2 vCPU, 7.6 GiB RAM, 8 GiB swap, 54 GB disk free, load ~0.1 |
| Docker | Engine 29.9.0, Compose 5.6.0; `docker` needs `sudo` |
| Running projects | `zwa-demo` (6 containers, checkout `/opt/zwa/repo` at `cf7f133` + local `preflight.sh` change + untracked `compose.amd64.yaml`), `zwa-restore-audit` (6), `zwa-prov-stage-r2` (Zebra only). `zwa-prov-stage` exited. All protected. |
| `zwa-demo` images | web `zwa-web:local` `f87b49a9…` (built 2026-10-13 from `cf7f133`, **without** the reviewed purchase-journey changes); engine `zwa-engine:local` `0f85a688…`; Zebra `zwa-zebra-amd64:0aef55ce` `2c3f3dde…`; Caddy 2.8.4 |
| `zwa-demo` site settings | `ZWA_SITE_ADDRESS=localhost`, `ZWA_PUBLIC_ORIGIN=https://localhost:8443`, `ZWA_BIND_ADDRESS=127.0.0.1`, ports 8088/8443 |
| Listening sockets | `0.0.0.0:22`; `127.0.0.1:8088/8443` (`zwa-demo` Caddy); `127.0.0.1:9088/9443` (`zwa-restore-audit` Caddy). No engine, portal or Zebra port is published. |
| Host firewall | ufw inactive; iptables INPUT policy ACCEPT (Docker manages its own chains) |
| From the internet | tcp/22 open; tcp/80 and 443 time out, so an AWS security group admits only SSH. Even with them open, Caddy listens on loopback only. |
| Domain / DNS | None configured. Reverse DNS is the EC2 default `ec2-<a-b-c-d>.<region>.compute.amazonaws.com`. |
| Capacity | Idle usage is negligible (Zebra state 3.7 MB at 336 blocks, about 11 KB a block). The demo caps settlements at 60 an hour (`maxSponsoredOrdersPerHour`), about 112 blocks each: at most about 75 MB of chain growth an hour. Purchases serialize on the one dealer wallet (about a minute each locally; AWS proving time is unmeasured because the benchmark has not run there). Three Zebra nodes and two full stacks share 2 vCPU. |

**Feasibility:** the existing host and the protected `zwa-demo` backend can serve the
demo. Only the web image and Caddy's site settings change. No new instance, database or
load balancer is needed.

## 3. Domain and TLS options (all free)

| Option | Trusted certificate | Notes |
|---|---|---|
| **A. A domain or subdomain the owner already controls** (an A record to `<host-ip>`) | Yes, Caddy obtains it automatically (Let's Encrypt, ZeroSSL fallback) | **Recommended.** Stable name, no third-party DNS. Changing DNS is an owner step. |
| **B. `<a-b-c-d>.sslip.io`** (already resolves to the host) | Usually: Caddy can obtain one | No account or DNS change. A third party answers DNS. `sslip.io` is **not** on the Public Suffix List, so all its users share one Let's Encrypt quota (50 new certificates per registered domain per week); issuance can fail when it is exhausted (ZeroSSL fallback may still work). Check issuance well before judging. |
| C. The EC2 default hostname | **No** | Let's Encrypt refuses `amazonaws.com` names by policy. |
| D. A free tunnel (e.g. a Cloudflare quick tunnel) | Yes, the tunnel's | No inbound ports, but a third party terminates TLS and sees session cookies; quick-tunnel URLs are random and unsupported for production. Not recommended. |

Options A and B need inbound **80 and 443** (ACME validation and HTTPS) and a stable
public IPv4. The instance already pays for its public IPv4, so an Elastic IP (to keep
the address across a stop/start) adds no cost while associated. Check that in the
console **(owner)**.

## 4. Security review for public judges

| Requirement | Status | Where |
|---|---|---|
| Session isolation | Met. Each judge gets a random `sid`; the backend re-binds every demo request to its live wallet by session hash and refuses other sessions, forged roles and accounts. | `exchange/portal.py` `handle`; `exchange/test_demo.py` session tests; M10 two-context browser test |
| Cookies | Met. HttpOnly, SameSite=Strict, Secure whenever `ZWA_PUBLIC_ORIGIN` is https; HMAC-signed; expire with the wallet (2 h). | `website/lib/exchange-auth.ts` |
| CSRF and origin | Met. Per-session HMAC token on every mutation; hosted mode requires Origin and Host to equal the configured https origin. | `exchange-auth.ts`, `lib/request-origin.ts`, `lib/private-orders.ts` |
| Expiry and recovery | Met. Expired sessions are refused; pre-signing orders are cancelled at expiry; settlements in flight finish. The browser keeps an unresolved purchase intent and recovers by lookup. | `demo_wallets.py`, `components/exchange/purchase.ts` |
| API authentication | Met. The web reaches the portal over the internal network with a 32+ character bearer token; fixed actions only; the portal is never published. | `portal_server.py`, `deploy/compose.yaml` |
| Purchase idempotency | Met. Same key returns the same order (ledger); the browser never mints a second key for an unresolved intent. | `order_ledger.request_purchase`, `purchase.ts` |
| Rate limits and quotas | Met. Web (token buckets): new sessions per client burst 3 then 1 a minute, globally burst 30 then 30 a minute; mutations per session burst 20 then 30 a minute; sign-in burst 5 then 5 a minute per client. Ledger: 3 wallets per client an hour, 60 an hour, 40 active, 5 orders per wallet, 5 units and 1,000,000 zat per order, one open order per wallet, 60 sponsored settlements an hour, sponsor reserve 12. | `api/demo/session`, `exchange/config.py` `DEMO_LIMITS` |
| Signer access | Met. The demo signer has no network listener; it reads the ledger and per-order Unix sockets in the engine volume, which the web container does not mount. | `exchange/demo_signer.py`, compose volumes |
| Browser-visible errors | Met. Portal errors are short fixed strings (≤200 characters); unexpected exceptions become a generic message and, in the browser, an "unknown outcome" (503), never a refusal or success. | `portal.py` `main`, `lib/exchange-route.ts` |
| Logs | Met. Portal logs path and status only; Caddy has no access log; no bodies, cookies or tokens are logged. | `portal_server.py`, `deploy/Caddyfile` |
| Unresolved orders | Met for honesty: `UNCERTAIN` is shown as unresolved, never verified. **Operational risk:** an `UNCERTAIN` order keeps the dealer wallet committed, so every judge's next quote waits until an operator resolves it. Monitor with the inspector preflight during judging. | `order-labels.ts` `verdict`, `order_ledger.WALLET_COMMITTED_STATES` |
| Repeated launches and exhaustion | Bounded by the caps above. A determined client with many addresses could use up the 60 wallets an hour and lock out judges for the rest of that hour; acceptable for a judging window, but watch it. | |
| Internal exposure | Met. Only Caddy publishes ports; engine, portal and Zebra sit on an `internal: true` network. The inspector checks that no engine or Zebra port is published. | compose, inspector preflight |
| Headers | Met. CSP, X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy, COOP (Next.js); HSTS (Caddy). | `website/next.config.ts`, `deploy/Caddyfile` |

**No code-level public-launch blocker was found.** Owner hardening items, not code:

1. The public `/sign-in` page accepts the provisioned dealer account, which can pause sales
   and change prices. Use a long random password (scrypt-hashed, 5 attempts a minute per
   client), or provision a fresh one for the judging window.
2. `npm audit` was not run in this review (it sends lockfile metadata to npm). Next.js
   15.5.27 and React 19.3.0 are newer than the late-2025 server-components fixes known to
   this review; run an audit before launch.
3. Consider stopping `zwa-restore-audit` and `zwa-prov-stage-r2` (stop only, no volume
   change) during judging to free CPU on the 2-vCPU host **(owner)**.

## 5. Deployment procedure (owner-approved steps only)

Variables used below (fill in; `HOST_NAME` is the option A or B name):

```sh
HOST=ubuntu@<host-ip>             # the existing instance
KEY=~/.ssh/zwa-ec2-key.pem
HOST_NAME=demo.example.org          # or <a-b-c-d>.sslip.io (the host IP with dashes)
TAG=judge-80abbd8
```

### 5.1 Build the web image (local, no host change)

From a clean archive of the reviewed commit, for the host's platform:

```sh
CTX=$(mktemp -d) && git archive 80abbd8 website exchange/catalog.json deploy/web .dockerignore | tar -x -C "$CTX"
docker buildx build --platform linux/amd64 -f "$CTX/deploy/web/Dockerfile" \
  --label org.zwa.git-revision=80abbd87b9f3fea48f843fd15ebe0e8802de4925 -t zwa-web:$TAG-amd64 --load "$CTX"
docker image inspect zwa-web:$TAG-amd64 --format '{{.Id}} {{.Architecture}}'   # record the ID; must say amd64
```

Done locally during this review: `zwa-web:judge-80abbd8-amd64` =
`sha256:f2baeb4b80d80bedc896bc0da93ee16505525d855de64282b5546541b5727590`, linux/amd64,
label `org.zwa.git-revision=80abbd8…`, containing the reviewed purchase-intent and verdict
code (its client chunk names equal the arm64 build's). A rebuild may get another image ID
(timestamps); record yours and compare it after the transfer.

### 5.2 Record and back up on the host (read-only plus one file copy) **(owner)**

```sh
ssh -i $KEY $HOST 'set -e; cd /opt/zwa/repo/deploy; W=~/zwa-judge-deploy-$(date -u +%Y%m%dT%H%M%SZ); mkdir -m 700 $W
  sudo -n docker ps -a --format "{{.Names}} {{.Image}} {{.Status}}" | sort > $W/ps-before.txt
  sudo -n docker volume ls -q | sort > $W/volumes-before.txt
  sudo -n docker image inspect zwa-web:local --format "{{.Id}}" > $W/web-image-before.txt
  cp -p .env $W/env-before        # mode 600; contains the portal token: never copy it off the host
  echo $W'
```

Take the existing paired-volume backup ([`zebra-provenance-migration.md`](zebra-provenance-migration.md),
`~/zwa-recovery`) if the owner wants a restore point for the ledger and chain before
judges arrive. The steps below never touch any volume.

### 5.3 Transfer the image **(owner)**

```sh
docker save zwa-web:$TAG-amd64 | gzip | ssh -i $KEY $HOST 'gunzip | sudo -n docker load'
ssh -i $KEY $HOST "sudo -n docker tag zwa-web:$TAG-amd64 zwa-web:$TAG && sudo -n docker image inspect zwa-web:$TAG --format '{{.Id}} {{.Architecture}}'"
```

The ID must equal the one recorded in 5.1. `zwa-web:local` is left in place for rollback.

### 5.4 Open the network path **(owner, AWS console)**

1. Security group of the instance: allow inbound TCP 80 and 443 from `0.0.0.0/0`
   (and `::/0` only if the instance has IPv6). Keep every other rule; never open
   8088/8443/9088/9443, 8790 or 18232.
2. Option A only: create the A record `HOST_NAME → <host-ip>`; wait until
   `dig +short $HOST_NAME` returns it.

### 5.5 Switch `zwa-demo` to the public name and the new web image **(owner)**

Edit exactly these lines in `/opt/zwa/repo/deploy/.env` (the token and the other lines stay):

```
ZWA_SITE_ADDRESS=<HOST_NAME>
ZWA_PUBLIC_ORIGIN=https://<HOST_NAME>
ZWA_BIND_ADDRESS=0.0.0.0
ZWA_HTTP_PORT=80
ZWA_HTTPS_PORT=443
ZWA_WEB_TAG=judge-80abbd8
```

Then recreate **only** web and Caddy; `--no-deps` keeps portal, order service, demo signer
and Zebra (and every volume) exactly as they are:

```sh
cd /opt/zwa/repo/deploy
W=<directory printed in 5.2>
C="sudo -n docker compose -f compose.yaml -f compose.amd64.yaml"
$C config --services > /dev/null                      # the edited .env parses
$C up -d --no-deps web caddy
$C ps
sudo -n docker ps -a --format "{{.Names}} {{.Image}} {{.Status}}" | sort | diff $W/ps-before.txt - || true
                                                      # only zwa-demo-web-1 and zwa-demo-caddy-1 may differ
sudo -n docker volume ls -q | sort | diff $W/volumes-before.txt -   # must print nothing
```

### 5.6 Verify before telling judges

```sh
# Backend health (read-only, from the workstation):
python3 tests/acceptance/real_settlement_inspect.py --project zwa-demo \
  --ssh $HOST --ssh-key $KEY --sudo preflight            # must be ok: true
# Public TLS: a trusted certificate for the name, and a wrong name rejected
curl --fail -sS -o /dev/null -w '%{http_code} verify=%{ssl_verify_result}\n' https://$HOST_NAME/
openssl s_client -connect $HOST_NAME:443 -servername $HOST_NAME -verify_hostname $HOST_NAME </dev/null 2>/dev/null | grep 'Verify return code: 0'
curl -sS -o /dev/null -m 5 http://$HOST_NAME/ -w '%{http_code} -> %{redirect_url}\n'   # 308 to https
nc -z -G 4 <host-ip> 8790 || echo "8790 closed (expected)"
```

Then, before announcing the URL (no other judge may be buying, or the "exactly one new
order" check cannot hold), run the real acceptance test once, against the public URL with
normal browser trust **(owner: it spends one disposable regtest purchase)**:

```sh
ZWA_REAL_PURCHASE=ONE ZWA_REAL_BASE=https://$HOST_NAME ZWA_REAL_PROJECT=zwa-demo \
ZWA_REAL_SSH=$HOST ZWA_REAL_SSH_KEY=$KEY ZWA_REAL_SUDO=1 ZWA_REAL_OUT=/private/tmp/zwa-public-e2e \
ZWA_PLAYWRIGHT_PACKAGE=/path/to/node_modules/playwright node tests/acceptance/real_browser_purchase.cjs
```

PASS requires the preflight, the browser flow and the independent verification to pass.
A STOPPED or ERROR result keeps everything as it is for review; do not retry blindly.

### 5.7 During judging

Every 15 to 30 minutes, run the read-only preflight above. If it reports an `UNCERTAIN`
or long-running order, purchases are queued behind it: inspect that order with
`verify --order …` and follow the operator recovery procedure before more judges buy.

## 6. Rollback (keeps all settlement data)

```sh
ssh -i $KEY $HOST
cd /opt/zwa/repo/deploy
W=<directory printed in 5.2>
cp -p $W/env-before .env                               # site, origin, bind, ports and web tag as before
C="sudo -n docker compose -f compose.yaml -f compose.amd64.yaml"
$C up -d --no-deps web caddy                           # back to zwa-web:local on loopback 8088/8443
sudo -n docker volume ls -q | sort | diff $W/volumes-before.txt -   # must print nothing
```

Then remove the security-group rules for 80/443 **(owner)** and, for option A, the DNS
record. The ledger, wallet homes, chain state and Caddy data volumes are never deleted or
recreated by deployment or rollback. Orders placed by judges stay in the ledger and keep
settling or expiring under the order service, which was never restarted.

## 7. Cost

**US$0 of additional infrastructure** for options A and B: the existing instance, its
existing public IPv4, Caddy's free ACME certificates, no new DNS service. Normal
data-transfer charges for judge traffic apply (small; a page view is a few hundred KB).
Option A's DNS record is free where the owner already runs the zone.

## 8. Remaining blockers before public judges

1. **(owner)** Choose the name: an owned domain (A) or `<a-b-c-d>.sslip.io` (B).
2. **(owner)** Open inbound 80/443 in the instance's security group; confirm a stable
   public IPv4 (Elastic IP).
3. **(owner)** Approve changing the protected `zwa-demo` `.env` and recreating its web and
   Caddy containers (section 5.5), plus the image transfer.
4. Run section 5.6, including one real public browser purchase, before announcing the URL.
5. Owner hardening items in section 4.
