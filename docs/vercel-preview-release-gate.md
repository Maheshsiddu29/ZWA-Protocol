# Vercel Preview release gate: judge exchange (remote mode)

**Scope: the first Preview deployment of branch `integration/vercel-native-exchange`. Not
Production.** The owner pushes; nothing here merges, deploys Production, changes the exchange
host or buys anything. Design and security model: [vercel-native-exchange.md](vercel-native-exchange.md).
`<exchange-host>` is the hosted exchange's public HTTPS name (kept out of this public repository).

Decisions in force for the Preview: exchange rate limits unchanged; no trusted client-address
forwarding; no exchange configuration change; no new secret on Vercel; no deployment-protection
bypass; no real purchase.

## 1. Source and merge impact

Production is `cf7f133` (= `main` = `origin/main`). The branch is a fast-forward of it: no merge
commits, one author (GitHub noreply address), no co-author lines.

| Area | Commits | What a Vercel build sees | Risk |
|---|---|---|---|
| Zebra provenance and AWS staging (`exchange/zebra_provenance.py`, `exchange/persistent_chain.py`, `deploy/scripts/*`, tests, docs) | 21 (`fdc1629`…`2175039`) | Nothing: Vercel builds `website/` and imports only `exchange/catalog.json`, which is unchanged | None for Vercel. On `main` they would ship in the next engine image built from `main`; 316 related tests pass (`exchange/test_{persistent_chain,zebra_provenance,deploy,provenance_stage}.py`, `benchmark/`). `deploy/compose.yaml`: comment only. |
| Benchmark harness (`benchmark/`, `deploy/benchmark/`, docs) | 13 (`f3fcd93`…`1b27445`) | Nothing | None for Vercel; tooling only. |
| Judge purchase journey (`website/`) | 6 (`a02815f`…`80abbd8`) | Yes: purchase intent and idempotency, unknown-outcome handling (503), Settlement-verified rule, Launch demo wording, wallet strip on `/exchange`, `npm test` script | Without remote-mode variables the site still fails closed exactly as today (no backend). This is the code already serving the hosted exchange. |
| Acceptance tooling and deployment plan | 4 (`94f5fb3`…`4e89072`) | Nothing | None. |
| Remote mode (`website/`, tests, docs) | 9 (`f9d97ca`…`0622749`) and this document | Yes, only when `ZWA_EXCHANGE_ORIGIN` is set | Section 2. |

No dependency, lockfile, `next.config.ts`, `tsconfig.json`, Vercel or build-setting change. No new
environment variable is required for the existing Production behaviour: with none set, the site is
the public site and every exchange route fails closed (`/api/exchange/market` → 403, as now).
Scans of the whole range found no credentials, host address, cloud account or resource IDs; the one
"PRIVATE KEY" string is a test fixture header with no key material.

## 2. Proxy security review

Files: `website/lib/remote-contract.ts`, `website/lib/remote-exchange.ts`, the 16 API routes,
`exchange-auth.ts`, `private-orders.ts`.

| Check | Result |
|---|---|
| Exact Origin and Host | Mutations need `Origin` and `Host` equal to `ZWA_PUBLIC_ORIGIN` (string equality, one origin). Tested refusals: none, `null`, foreign, the exchange's, `http:`, other port, upper case, trailing slash, look-alike suffix, path-embedded, foreign Host, `X-Forwarded-Host` spoof. Only then is the request forwarded with the exchange's origin. |
| Exchange TLS | Node's default verification, no custom agent. Remote mode is invalid with `NODE_TLS_REJECT_UNAUTHORIZED=0` (added in `bd01015`). An untrusted certificate yields no data (502/503). |
| Cookies | Re-issued by the site only on demo start/end: `Secure; HttpOnly; SameSite=Strict; Path=/`, no Domain (host-only). Other upstream cookies dropped; malformed or duplicate session cookie → 503. |
| Exchange session and CSRF | Unchanged on the exchange: HMAC session, live wallet binding, per-session CSRF on every mutation. Another session's token is refused (403). |
| No privileged credentials on Vercel | Three non-secret variables. Remote mode is invalid next to `ZWA_SESSION_SECRET`, `ZWA_ROLE_ACCOUNTS`, `ZWA_PORTAL_URL`, `ZWA_PORTAL_TOKEN`, `ZWA_PRIVATE_EXCHANGE_ROOT`. The dealer password exists only on the exchange host. |
| No sensitive caching | Every proxied answer `Cache-Control: no-store`; session pages are dynamic (`private, no-store`); upstream fetches `cache: "no-store"`; per-request memoization only. |
| No logging of sessions | No `console.*` in the website; upstream errors are swallowed; Next.js does not log fetches in production. Only `zwa_session` goes upstream (never Vercel's `_vercel_jwt` or other cookies). |
| No arbitrary upstream URL | The exchange origin comes only from the environment; paths are constants or `/api/exchange/orders/{32 hex}` validated before forwarding; redirects are not followed (`redirect: "manual"`), and a 3xx becomes an error. |
| Status propagation | 200, 400, 403, 404, 409, 413, 429, 503 with a JSON body pass through; anything else, non-JSON, oversize, timeout or network error → 503 `outcome: "unknown"` for a state change, 502 for a read. |
| Unknown-outcome recovery and idempotency | Tested: answers lost inside the exchange (portal), and a buy the exchange committed whose answer was lost twice between exchange and site, each give exactly one order with the browser's one key; a hung exchange gives "unknown" after 28 s (30 s function limit). |
| No automatic duplicate confirmation | The proxy never retries. On an unknown confirmation the page re-reads the order and never re-sends. Only the buy is re-sent, with the same key, by design. |
| Judge isolation | Tested with two and three concurrent judges: no access to another's order (page 404, API refused), separate cookies and tokens, empty portfolio. |
| Contract change on the exchange page | Session discovery returns "signed out" for a reordered or renamed viewer, a renamed or longer token, two tokens or another payload format (unit tests); judges then cannot act (fail closed). |

Finding (accepted for the Preview, open for Production): all judges reach the exchange from Vercel's
address, so the exchange's per-client caps are shared (section 5, gate 7).

## 3. Preview configuration

Vercel → Project `zwaprotocol` → Settings → Environment Variables → **Preview**, restricted to the
Git branch `integration/vercel-native-exchange`:

| Name | Value |
|---|---|
| `ZWA_EXCHANGE_ORIGIN` | `https://<exchange-host>` |
| `ZWA_PUBLIC_ORIGIN` | the branch's stable Preview origin, exactly as Vercel shows it under the deployment's Domains (`https://zwaprotocol-git-…vercel.app`, no trailing slash). Vercel may shorten long branch hosts, so copy it; do not construct it. |
| `ZWA_TRUST_PROXY` | `1` |

Must **not** exist for Preview (any of them disables remote mode, by design): `ZWA_SESSION_SECRET`,
`ZWA_ROLE_ACCOUNTS`, `ZWA_PORTAL_URL`, `ZWA_PORTAL_TOKEN`, `ZWA_PRIVATE_EXCHANGE_ROOT`,
`NODE_TLS_REJECT_UNAUTHORIZED`. Also leave `ZWA_DEMO_MODE`, `ZWA_CHAIN_RPC_PORT` and
`NODE_EXTRA_CA_CERTS` unset. Do not change the Production variables.

Deployment Protection (Settings → Deployment Protection): keep **Vercel Authentication** on for
Preview deployments. The Preview is then visible only to signed-in members of the team. Mutations
are accepted only on the branch host named in `ZWA_PUBLIC_ORIGIN`: the per-deployment
`zwaprotocol-<hash>-….vercel.app` URL renders but refuses state changes (403).

These settings were prepared, not applied: this review had no Vercel access, so Root Directory
(expected `website`), Production Branch (expected `main`), protection and existing variables must
be confirmed by the owner in the dashboard.

## 4. Owner push procedure

```sh
cd <worktree>/ZWA-Brand-Integration
git status                                  # clean, on integration/vercel-native-exchange
git log --oneline -1                        # the reviewed head
git fetch origin && git log --oneline origin/main -1   # still cf7f133; otherwise stop and re-review
git push origin integration/vercel-native-exchange     # owner only
```

1. Before pushing, confirm in Vercel that the Production Branch is `main`, so this push can only
   create a Preview.
2. The first Preview builds with no remote-mode variables: it is the public site and fails closed.
3. Copy that deployment's branch host, create the three Preview variables (section 3), then
   Deployments → that Preview → Redeploy. Variables apply only to new deployments.

## 5. Preview verification

Record the baseline right before step 1 of section 4:

```sh
curl -s https://<exchange-host>/api/exchange/market | python3 -c 'import json,sys; m=json.load(sys.stdin); print(m["chainActive"], m["chainHeight"], m["demo"]["dealerPolicy"], [(i["instrumentId"], i["availableUnits"]) for i in m["instruments"]])'
```

(At review time, 2026-10-15: `True 669 ACTIVE`, treasury 900, credit 899, gold 899 units.)

### Stage A: read-only (no session, no state change anywhere). Then stop.

Use a fresh private browser window signed in to Vercel, with DevTools open (Network, "Preserve log").

| Gate | Check | Pass when |
|---|---|---|
| 0 | `curl -sI <preview-url>` from a terminal | 401 or a redirect to Vercel login (protection is on) |
| 1 | Open the Preview branch URL | 200; build log shows `next build` success with the `/api/exchange/*` routes |
| 2 | Compare `/` with `https://zwaprotocol.vercel.app/` side by side | Same sections and copy. Expected differences only: the call to action reads **Launch demo** (not "Sign in"/"Open the exchange"), and the market table shows prices, units and "Scanned at block N" instead of "Not listed" |
| 3 | Open `/exchange`, then each of the three asset pages | Three listings with units; no console errors; every Network entry is the Preview host with status 200 (or 304) |
| 4 | Open `<preview>/api/exchange/market` and compare with the baseline command | Same instruments, units and prices; response header `cache-control: no-store` |
| 5a | In the DevTools console of the Preview (no session yet), run the snippet below | `market 200 no-store`, `wallet 403 no-store`, `orders 403` (refused by the site, not forwarded), `login 404`, `dealer-policy 404` |
| 9 | Visit `/`, `/exchange`, asset pages, `/wallet`, `/portfolio`, `/verify`, `/sign-in`, `/seller` | No navigation or request to `<exchange-host>`. The only reference to it is the explicit "Check this transaction on the exchange host" link on `/verify?tx=…` |
| 8 | Re-run the baseline command; optionally the owner's read-only inspector preflight on `zwa-demo` | `chainActive` true, dealer ACTIVE; preflight ok |
| 10 | Compare with the baseline | Units unchanged: no purchase occurred |
| 7 | Report the limits (nothing to measure in Stage A) | See "Rate limits" below |

```js
const probe = async (path, init) => { const r = await fetch(path, init); return `${path.split('/').pop()} ${r.status} ${r.headers.get('cache-control') ?? ''}`; };
const post = { method: 'POST', headers: { 'content-type': 'application/json', 'x-zwa-csrf': '0'.repeat(64) }, body: '{}' };
console.log((await Promise.all([probe('/api/exchange/market'), probe('/api/exchange/wallet'),
  probe('/api/exchange/orders', post), probe('/api/exchange/login', post), probe('/api/exchange/dealer-policy', post)])).join('\n'));
```

**Stop after Stage A and report.**

### Stage B: sessions (needs separate owner approval; still no purchase)

Gates 5 and 6 need judge sessions. Each Launch demo creates one disposable wallet on `zwa-demo`,
funded by a regtest transaction, and counts against the shared caps. Never press **Buy**.

| Gate | Check | Pass when |
|---|---|---|
| 5 | Window 1: Launch demo → `/wallet` | "Ready to trade"; Application → Cookies: `zwa_session` on the Preview host only, Secure, HttpOnly, SameSite Strict, no Domain; nothing stored for `<exchange-host>`; `document.cookie` does not show it |
| 5 | Window 1 console: `await fetch('/api/exchange/orders', {method:'POST', headers:{'content-type':'application/json','x-zwa-csrf':'0'.repeat(64)}, body:JSON.stringify({offerId:'0'.repeat(32), quantity:1, idempotencyKey:'buy-gate-check'})}).then(r => r.status)` | `403` (the exchange's CSRF check refuses it; nothing is created) |
| 6 | Window 2 (another browser or profile): Launch demo | A different wallet address on `/wallet`; `/portfolio` shows "No orders yet."; window 1 unchanged |
| 7 | If either launch answers "too many demo sessions…" | Expected effect of the shared caps; record which message (section "Rate limits") |
| — | End both sessions ("End session and delete wallet") | Back on `/` with Launch demo; the cookie is gone |

### Rate limits (gate 7 report)

- Site (per judge address, per function instance): 3 new sessions, then 1 a minute.
- Exchange web (per client address): 3 new sessions, then 1 a minute. Through Vercel, the client
  address is Vercel's egress address, shared by all judges.
- Exchange ledger: **3 wallets per client address per hour** (shared through Vercel, as above),
  60 wallets an hour and 40 active in total, 5 orders per wallet. Measured locally: a 4th judge
  through the site gets `429 "too many demo sessions from this connection; try again later"`.
- Preview, Production and the direct exchange URL all draw on the same exchange limits.

## 6. Remaining Production blockers

1. **Shared per-client caps**: decide before Production (accept for a short window, raise
   `maxWalletsPerClientPerHour` up to 20 on the exchange, or a trusted-address design). Measure on
   the Preview how many egress addresses Vercel uses (Stage B).
2. **No real purchase through Vercel yet**: needs separate approval, with the inspector before and
   after.
3. **Merging to `main`** ships every commit since `cf7f133` (section 1). The engine-side changes
   only take effect when an engine image is rebuilt from `main`.
4. **Exchange page coupling**: any change of the exchange's web image requires re-running the
   read-only check (`tests/acceptance/vercel_remote_readonly.cjs`) and Stage A.
5. **Production variables** and the Production origin `https://zwaprotocol.vercel.app` (any other
   Production domain would render but refuse state changes).
6. **Vercel Hobby** usage and terms during judging (no paid resources).
