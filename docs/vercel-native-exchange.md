# Judge exchange on the Vercel site (remote mode)

**Status: built and verified locally; not deployed.** Nothing in this document has been run
on Vercel or on the exchange host, apart from the read-only checks in section 5. Every
step marked **(owner)** needs explicit approval. No new paid service is used.

Goal: judges complete the real purchase lifecycle on the submitted Vercel domain while
settlement keeps running on the existing hosted exchange (`zwa-demo`), untouched.

`<exchange-host>` below is the public HTTPS name of the hosted exchange (kept out of this
public repository).

## 1. Audit

| | Vercel production | Verified judge frontend | Hosted exchange |
|---|---|---|---|
| Code | `website/` at `main` (`cf7f133`) | `website/` at `80abbd8` (the same Next.js app plus the purchase-journey work) | web image built from `80abbd8`, behind Caddy |
| Exchange | None: no variables, every exchange route fails closed (`/api/exchange/market` → 403) | Needs the session secret and portal token on its server | Holds the secret, portal token and role accounts |
| Functions | Next.js on Vercel, region `iad1` | | Caddy replaces `X-Forwarded-For` with the peer address |

What the verified pages need: the root layout and every exchange page read the session cookie
**and verify it with `ZWA_SESSION_SECRET`** on the server, call the portal with `ZWA_PORTAL_TOKEN`,
and render the per-session CSRF token (an HMAC with the same secret) into the page. A Vercel
deployment must hold neither secret, so it can neither verify a session nor produce a CSRF token.

Options considered:

1. **Vercel rewrites of the exchange pages and the API to the exchange.** Rejected. The exchange's
   pages load their scripts from `/_next/static/…` of a *different* Next.js build, which collides
   with the site's own `/_next` paths; and the exchange accepts a state change only when Origin and
   Host equal its own origin, which an edge rewrite cannot change. It also cannot be verified
   without deploying.
2. **Give Vercel the session secret and portal access.** Forbidden (and the portal is on an internal
   network).
3. **Chosen: remote mode.** The site renders the verified pages itself, with the verified client
   components unchanged; its server reads data from the exchange's JSON API, proxies the judge's
   API calls, and takes the session and CSRF token from the exchange's own rendering of `/wallet`.

## 2. Design

```
Browser ──https──▶ zwaprotocol.vercel.app  (Next.js, remote mode, no secrets)
                     │ pages: GET <exchange>/wallet (session + CSRF), GET <exchange>/api/exchange/…
                     │ API:   fixed routes proxied, see table
                     └──https──▶ <exchange-host>  (Caddy → web → portal → engine; unchanged)
```

Remote mode is on only when `ZWA_EXCHANGE_ORIGIN` and `ZWA_PUBLIC_ORIGIN` are two distinct, exact
HTTPS origins (single literal host names, no path, wildcard or credentials) **and** none of
`ZWA_SESSION_SECRET`, `ZWA_ROLE_ACCOUNTS`, `ZWA_PORTAL_URL`, `ZWA_PORTAL_TOKEN`,
`ZWA_PRIVATE_EXCHANGE_ROOT` is set. Otherwise it is "invalid": every exchange route answers 503,
no demo is offered (`lib/remote-contract.ts`, `remoteConfig`).

| Site route | Remote mode | Upstream |
|---|---|---|
| `POST /api/demo/session` | proxied (start) | same path |
| `POST /api/demo/end` | proxied (end) | same path |
| `GET /api/exchange/market`, `GET /api/exchange/wallet`, `GET /api/exchange/orders`, `GET /api/exchange/orders/{id}`, `GET …/{id}/receipt` | proxied (read) | same path |
| `POST /api/exchange/orders` (buy), `POST …/{id}/confirm`, `POST …/{id}/cancel` | proxied (mutation) | same path |
| `login`, `logout`, `dealer-policy`, `offers`, `offers/{id}/price`, `POST wallet` (pair), `wallet/unpair` | **404**, never forwarded | — |
| Pages `/`, `/exchange`, `/exchange/{asset}`, `/wallet`, `/orders/{id}`, `/portfolio` | rendered by the site | JSON API reads + `GET /wallet` |
| `/sign-in` | "operator sign-in is not available on this site" | — |
| `/verify?tx=` | public testnet receipts as before; the demo-chain lookup links to `<exchange-host>/verify?tx=` (the exchange has no JSON API for it) | — |

Session discovery (`lib/remote-exchange.ts`, `remoteSession`, once per render): if the browser
has a well-formed `zwa_session` cookie whose *unverified* claims say "demo" and "not expired", the
site fetches `<exchange>/wallet` with that cookie. The exchange verifies the signature, expiry and
live wallet binding and renders the page; the site accepts the session only if the inline React
Server Components payload contains exactly one demo-buyer viewer whose id equals the cookie's,
exactly one CSRF field, and it is 64 hex characters. Anything else: signed out.

## 3. Security requirements

| Requirement | How remote mode meets it | Evidence |
|---|---|---|
| Secure, HttpOnly, SameSite cookies | The exchange's session cookie is never passed through raw. On start (200 only) the site validates its value and lifetime (1 s to 24 h) and re-issues it `Secure; HttpOnly; SameSite=Strict; Path=/`, host-only on the site. On end (200 only) it clears it. Every other upstream `Set-Cookie` is dropped; a malformed or duplicated one fails the request. | unit `sessionCookieChange`; e2e cookie attributes; browser jar holds no cookie for the exchange |
| CSRF enforcement | Unchanged at the exchange: the per-session HMAC token issued by the exchange is required on every mutation. The site additionally requires a 64-hex `x-zwa-csrf` header and a session cookie before forwarding. | e2e: A's token with B's session → 403 |
| Strict trusted Origin/Host | The site requires `Origin` and `Host` to equal `ZWA_PUBLIC_ORIGIN` exactly (existing `requestOrigin`). Only then does it send the request upstream with the **exchange's** origin, which the exchange checks as before. No wildcard, no list. | e2e: no/foreign/exchange Origin, foreign Host → 403 |
| Session isolation | No server-side state: each render and proxied call carries only that browser's cookie; the exchange binds every demo request to its live wallet. The session lookup is memoized per request only. | e2e: B cannot read A's order (page 404, API ≠ 200), portfolio empty |
| Stable purchase idempotency | Bodies are forwarded unchanged (re-serialized JSON ≤ 2 KB), so the browser's one idempotency key reaches the ledger. A lost answer (network error, timeout, 5xx, non-JSON) to a state change becomes `503 {outcome:"unknown"}`, which the verified client resolves with the same key or by lookup. | e2e: two lost buy answers → one order; outage → unknown, nothing applied; resume uses the original key |
| No-store for private responses | Every proxied response is `Cache-Control: no-store`; pages are dynamic (`private, no-store`). | e2e and read-only probe headers |
| Sensitive headers and credentials | Upstream gets only `accept`, `cookie: zwa_session=…`, and for POST `origin`, `content-type`, `x-zwa-csrf`. Downstream gets only the JSON body, status and the re-issued cookie: no `Server`, `Set-Cookie` or other upstream headers. The site holds no secret; remote mode refuses to run next to one. | unit `remoteConfig`; e2e guard instance |
| No signing secrets on Vercel or in browsers | Three non-secret variables only. No `NEXT_PUBLIC_*`. | |
| No internal Docker ports | The site talks only to the public HTTPS name, which Caddy serves; the portal stays internal. | |
| Fail closed | Invalid configuration → 503 everywhere; session not provable → signed out (no CSRF, no mutation possible); unexpected upstream status, redirect or size → error, never success. | e2e step 0, forged and expired cookies |

## 4. Known limitations and risks

1. **Shared per-client caps (owner decision before announcing).** Caddy on the exchange sees every
   judge as the Vercel function's address, so the exchange's per-client limits apply to all judges
   using the site together: the web burst limit (3, then 1 new session a minute) and the ledger's
   **3 new wallets per client address per hour**. Measured locally: a 4th judge was refused with
   `429 "Too many demo sessions were started. Try again in a minute."`, and after 65 s with
   `429 "too many demo sessions from this connection; try again later"`. How many egress addresses
   Vercel uses is unknown until deployed. The site's own per-judge limit (by Vercel's client
   address) still applies first. Options, each a separate owner-approved backend change:
   (a) accept it for a small judging window; (b) raise `maxWalletsPerClientPerHour` (schema max 20)
   in the exchange's demo policy; (c) a backend change that trusts a client address only from the
   site, which needs a shared proxy credential on Vercel (a new secret, so not done here).
   Global caps (60 wallets an hour, 40 active) are unchanged by any option.
2. **Session discovery depends on the exchange's page format.** It reads the `/wallet` page of the
   web image now on the exchange (`80abbd8`). A different web image there may change that payload;
   the site then fails closed (judges appear signed out). Re-run section 5 after any exchange web
   change.
3. **Demo-chain lookup** on `/verify` opens the exchange host (the receipt on the order page is
   already re-checked against the live chain by the exchange).
4. **Exchange outage**: pages render, judges appear signed out, and Launch demo answers "The demo
   service is unavailable." No state is lost; sessions resume when the exchange is back.
5. **Load**: each page view by a judge adds one `/wallet` render on the exchange (one portal read).
6. **Operator desk** stays on the exchange host only.
7. **Not yet done**: a real purchase through the site (owner-gated, section 6); measuring Vercel
   egress addresses; true wallet expiry (two hours) through the site (the logic is the exchange's;
   ended and expired cookies were tested).

## 5. Verification (2026-10-15, local; read-only against the real exchange)

| Check | Result |
|---|---|
| `npm run lint` (tsc), `npm run build`, `npm test` | pass; 22/22 unit tests (6 new) |
| `tests/acceptance/vercel_remote_e2e.cjs` with `ZWA_EXCHANGE_WEB_DIR` = a `git archive` build of `80abbd8` (TEST DOUBLES: fake chain, proofs and tx-tool) | **13/13**, plus the two shared-cap findings above |
| `tests/acceptance/web_purchase_e2e.cjs` (local mode, regression) | 9/9 |
| `tests/acceptance/vercel_remote_readonly.cjs` against `<exchange-host>` | **7/7**: live market through the site equals the exchange's (3 listings, chain height 669, dealer ACTIVE); 13 pages and 29 referenced assets load; forged cookie rejected by the real exchange; no session started, no state change sent |
| Landing page vs current production (text diff) | Same content; the call to action becomes **Launch demo** and the market table shows live prices and inventory |

## 6. Deployment (owner)

Prerequisite: decide on limitation 1.

1. **Preview (owner):** push branch `integration/vercel-native-exchange`. In Vercel → Settings →
   Environment Variables, add for **Preview** (scoped to that branch):
   `ZWA_EXCHANGE_ORIGIN=https://<exchange-host>`,
   `ZWA_PUBLIC_ORIGIN=https://<the branch's stable preview origin, exactly as Vercel shows it>`,
   `ZWA_TRUST_PROXY=1`. Redeploy the preview. Do **not** add any other ZWA variable.
2. **Check the preview (owner):** open it while signed in to Vercel (previews are protected by
   default): landing, `/exchange`, Launch demo, wallet ready. Optionally one real purchase in the
   browser **(owner: spends one regtest purchase on `zwa-demo`)**, then the existing read-only
   inspector against `zwa-demo`.
3. **Production (owner):** add the same three variables for **Production** with
   `ZWA_PUBLIC_ORIGIN=https://zwaprotocol.vercel.app`. (Setting them before the code is live changes
   nothing: the current `main` ignores `ZWA_EXCHANGE_ORIGIN`.) Merge the reviewed branch into `main`
   and push; Vercel builds production from `main`. Note that this branch also carries the earlier
   local commits since `cf7f133` (benchmark harness, judge purchase journey, docs).
4. **Verify production:**
   `ZWA_REAL_EXCHANGE=https://<exchange-host> ZWA_REAL_SITE=https://zwaprotocol.vercel.app node tests/acceptance/vercel_remote_readonly.cjs`
   must print 7/7. Then one real purchase through `https://zwaprotocol.vercel.app` before announcing
   **(owner)**, with the inspector preflight before and verification after, as in
   [public-judge-deployment-plan.md](public-judge-deployment-plan.md) 5.6.

The exchange host is not touched by any step.

## 7. Rollback

- **Fastest:** Vercel → Deployments → the previous production deployment → *Instant Rollback*.
- **Or:** remove `ZWA_EXCHANGE_ORIGIN` from Production and redeploy: the site returns to the public
  site with every exchange route failing closed.

Either way the exchange keeps every order and wallet; sessions started on the site expire on the
exchange (two hours) and their cookies on the site become inert.

## 8. Cost

No new service. Vercel Hobby functions (each proxied call and page render is one invocation; a
purchase with status polling is a few hundred) and the existing exchange host. Check the Vercel
Usage tab during judging, and that the Hobby plan's terms fit the use.
