#!/usr/bin/env node
/** Remote-exchange mode (the Vercel site) end to end, on loopback, against TEST DOUBLES.
 *
 *   (cd website && npm run build)
 *   ZWA_PLAYWRIGHT_PACKAGE=/path/to/node_modules/playwright node tests/acceptance/vercel_remote_e2e.cjs
 *
 * Production shape, reproduced locally:
 *
 *   Chrome ──https──▶ "site" (this repo's website, remote mode: ZWA_EXCHANGE_ORIGIN, no secrets)
 *                       │  edge proxy: TLS, X-Forwarded-For = the judge (as Vercel sets it)
 *                       └──https──▶ "exchange" (a website build in hosted mode, ZWA_PUBLIC_ORIGIN, secrets)
 *                                     Caddy-like proxy: TLS, X-Forwarded-For replaced by the peer address
 *                                     └──▶ tests/acceptance/web_test_double_exchange.py (real portal, ledger,
 *                                          order service and demo signer; FAKE chain, proofs and tx-tool)
 *
 * ZWA_EXCHANGE_WEB_DIR may point at another built website directory (for example a `git archive` of the
 * exact commit deployed on the exchange host) to act as the exchange. Default: this repo's website.
 * Proves the site's proxy, session, CSRF, cookie, isolation and recovery behaviour. It proves NOTHING
 * about real settlement: no transaction, proof or block in this run is real. ZWA_E2E_SHOTS=<dir> saves screenshots.
 */
const assert = require('node:assert/strict');
const { execFileSync, spawn } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const https = require('node:https');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const { chromium } = require(process.env.ZWA_PLAYWRIGHT_PACKAGE || 'playwright');

const repo = path.resolve(__dirname, '..', '..');
const siteDir = path.join(repo, 'website');
const exchangeDir = path.resolve(process.env.ZWA_EXCHANGE_WEB_DIR || siteDir);
const ports = { siteWeb: 3331, exchangeWeb: 3332, guardWeb: 3333, site: 4431, exchange: 4432, guard: 4433 };
const site = `https://localhost:${ports.site}`;
const exchange = `https://127.0.0.1:${ports.exchange}`;
const shots = process.env.ZWA_E2E_SHOTS || '';
const children = [];
const servers = [];
const results = [];
const findings = [];
const pass = (name, detail = '') => { results.push(name); process.stdout.write(`PASS ${name}${detail ? `  ${detail}` : ''}\n`); };
const finding = (text) => { findings.push(text); process.stdout.write(`FINDING ${text}\n`); };
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'zwa-remote-e2e-'));

function certificate() {
  const key = path.join(tmp, 'key.pem'), cert = path.join(tmp, 'cert.pem');
  execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2', '-subj', '/CN=localhost',
    '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1', '-keyout', key, '-out', cert], { stdio: 'ignore' });
  return { key: fs.readFileSync(key), cert: fs.readFileSync(cert), certPath: cert };
}

/** A TLS front. "edge": X-Forwarded-For is the judge's address (Vercel overwrites it with the client IP;
 *  here the test names the judge in x-test-client). "caddy": X-Forwarded-For is replaced by the peer. */
function front(tls, listen, upstream, mode) {
  const server = https.createServer({ key: tls.key, cert: tls.cert }, (req, res) => {
    if (mode === 'caddy' && front.exchangeDown) { req.socket.destroy(); return; }
    if (mode === 'caddy' && front.hang) { front.hung.push(req); return; }   // never answers, never forwards
    const route = `${req.method} ${req.url.split('?')[0]}`;
    const drop = mode === 'caddy' && front.dropResponses.route === route && front.dropResponses.count > 0;
    if (drop) front.dropResponses.count -= 1;
    const headers = { ...req.headers };
    const judge = headers['x-test-client'];
    delete headers['x-test-client'];
    headers['x-forwarded-for'] = mode === 'edge' && judge ? String(judge) : req.socket.remoteAddress.replace(/^::ffff:/, '');
    if (mode === 'caddy') front.exchangeRequests.push(`${req.method} ${req.url.split('?')[0]}`);
    let size = 0;
    const out = http.request({ host: '127.0.0.1', port: upstream, method: req.method, path: req.url, headers }, (reply) => {
      // The exchange has answered (and committed); the answer is lost on the way back to the site.
      if (drop) { reply.resume(); reply.on('end', () => req.socket.destroy()); return; }
      res.writeHead(reply.statusCode, reply.rawHeaders);
      reply.pipe(res);
    });
    out.on('error', () => { if (!res.headersSent) res.writeHead(502); res.end(); });
    req.on('data', (chunk) => { size += chunk.length; if (size > 65536) { req.destroy(); } else out.write(chunk); });
    req.on('end', () => out.end());
  });
  servers.push(server);
  return new Promise((resolve) => server.listen(listen, '127.0.0.1', resolve));
}
front.exchangeRequests = [];
front.exchangeDown = false;
front.hang = false;
front.hung = [];
front.dropResponses = { route: '', count: 0 };

function startBackend() {
  const child = spawn('python3', ['tests/acceptance/web_test_double_exchange.py', '--pace', '0.25'],
    { cwd: repo, stdio: ['ignore', 'pipe', 'inherit'] });
  children.push(child);
  return new Promise((resolve, reject) => {
    readline.createInterface({ input: child.stdout }).once('line', (line) => resolve(JSON.parse(line)));
    child.once('exit', (code) => reject(new Error(`backend exited ${code}`)));
  });
}

async function startNext(dir, port, env) {
  const child = spawn(path.join(dir, 'node_modules/.bin/next'), ['start', '-H', '127.0.0.1', '-p', String(port)],
    { cwd: dir, env: { PATH: process.env.PATH, HOME: process.env.HOME, NODE_ENV: 'production', ...env }, stdio: ['ignore', 'ignore', 'inherit'] });
  children.push(child);
  for (let i = 0; i < 60; i++) {
    try { if ((await fetch(`http://127.0.0.1:${port}/privacy`)).ok) return child; } catch { /* starting */ }
    await sleep(500);
  }
  throw new Error(`next on ${port} did not start`);
}

let control = '';
let tlsAgent;
async function fault(body) {
  assert.equal((await fetch(`${control}/fault`, { method: 'POST', body: JSON.stringify(body) })).status, 200);
}
async function ledgerOrders(buyerId) {
  const state = await (await fetch(`${control}/state`)).json();
  assert.deepEqual(state.loopErrors, [], 'the exchange loop must not fail');
  return state.orders.filter((o) => o.buyer_id === buyerId);
}

/** A raw HTTPS request to the site (or to the site's plain upstream with `direct`), trusting the test CA. */
function raw(method, url, { headers = {}, body, direct = false } = {}) {
  const target = new URL(url);
  const lib = direct ? http : https;
  return new Promise((resolve, reject) => {
    const req = lib.request({ host: target.hostname, port: target.port, method, path: target.pathname + target.search,
      headers: { ...headers, ...(body ? { 'content-length': Buffer.byteLength(body) } : {}) }, agent: direct ? undefined : tlsAgent }, (res) => {
      let text = '';
      res.on('data', (c) => { text += c; });
      res.on('end', () => { let json = null; try { json = JSON.parse(text); } catch { /* not json */ }
        resolve({ status: res.statusCode, headers: res.headers, text, json }); });
    });
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

async function shot(page, name) {
  if (!shots) return;
  fs.mkdirSync(shots, { recursive: true });
  await page.screenshot({ path: path.join(shots, `${name}.png`), fullPage: true });
}

/** One judge: its own Chrome context and client address; every browser request is recorded. */
async function judge(browser, n) {
  const context = await browser.newContext({ ignoreHTTPSErrors: true, extraHTTPHeaders: { 'x-test-client': `198.51.100.${n}` } });
  const hosts = new Set();
  context.on('request', (request) => hosts.add(new URL(request.url()).host));
  const page = await context.newPage();
  return { context, page, hosts };
}

async function launch(j) {
  await j.page.goto(site, { waitUntil: 'networkidle' });
  await j.page.getByRole('button', { name: 'Launch demo' }).first().click();
  await j.page.waitForURL('**/wallet', { timeout: 30000 });
  await j.page.getByRole('link', { name: 'Browse assets' }).waitFor({ timeout: 60000 });
}

const sessionCookie = async (context) => (await context.cookies(site)).find((c) => c.name === 'zwa_session');
const csrfOf = async (page) => {
  const html = await page.content();
  const all = [...html.matchAll(/csrf\\?":\\?"([0-9a-f]{64})/g)].map((m) => m[1]);
  return all[0] || null;
};
const orderIdFrom = (page) => page.url().match(/\/orders\/([0-9a-f]{32})/)[1];
const headline = (page) => page.locator('h1.big-state').innerText();
async function apiOrder(context, id) {
  const response = await context.request.get(`${site}/api/exchange/orders/${id}`);
  assert.equal(response.status(), 200);
  return response.json();
}
async function openAsset(page, instrument = 'zwa-gold-reference') {
  await page.goto(`${site}/exchange/${instrument}`, { waitUntil: 'networkidle' });
  await page.getByText('Network: Zcash regtest').waitFor();
}
async function confirmQuote(page) {
  await page.getByText('Review your quote').waitFor({ timeout: 60000 });
  await page.getByLabel('I confirm these exact terms.').check();
  await page.getByRole('button', { name: 'Confirm purchase' }).click();
}
async function watch(page, context, id, done, timeout = 120000) {
  const seen = [];
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const text = await headline(page);
    if (seen.at(-1) !== text) seen.push(text);
    if (text === 'Settlement verified') {
      const order = await apiOrder(context, id);
      assert.equal(order.state, 'RECEIPT_AVAILABLE', 'verified shown before RECEIPT_AVAILABLE');
      assert.ok(order.receipt && order.receipt.durability && order.effects && !order.settlementDropped);
    }
    if (await done(text)) return seen;
    await sleep(250);
  }
  throw new Error(`timed out; headlines seen: ${seen.join(' → ')}`);
}

async function main() {
  const tls = certificate();
  tlsAgent = new https.Agent({ ca: tls.cert });
  const backend = await startBackend();
  assert.equal(backend.testDoubles, true);
  control = backend.control;
  const exchangeEnv = { ZWA_DEMO_MODE: '1', ZWA_PORTAL_URL: backend.portalUrl, ZWA_PORTAL_TOKEN: backend.portalToken,
    ZWA_SESSION_SECRET: crypto.randomBytes(32).toString('hex'), ZWA_ROLE_ACCOUNTS: '[]', ZWA_PORTFOLIO_SELLER_ID: 'dealer',
    ZWA_TRUST_PROXY: '1', ZWA_PUBLIC_ORIGIN: exchange };
  // The site gets ONLY public configuration: no session secret, portal URL or token, role accounts or state root.
  const siteEnv = { ZWA_EXCHANGE_ORIGIN: exchange, ZWA_PUBLIC_ORIGIN: site, ZWA_TRUST_PROXY: '1', NODE_EXTRA_CA_CERTS: tls.certPath };
  await startNext(exchangeDir, ports.exchangeWeb, exchangeEnv);
  await startNext(siteDir, ports.siteWeb, siteEnv);
  await front(tls, ports.exchange, ports.exchangeWeb, 'caddy');
  await front(tls, ports.site, ports.siteWeb, 'edge');

  // 0. Fail closed: remote mode next to a backend secret serves no exchange at all.
  {
    const guard = `https://localhost:${ports.guard}`;
    await startNext(siteDir, ports.guardWeb, { ...siteEnv, ZWA_PUBLIC_ORIGIN: guard, ZWA_SESSION_SECRET: exchangeEnv.ZWA_SESSION_SECRET });
    await front(tls, ports.guard, ports.guardWeb, 'edge');
    const market = await raw('GET', `${guard}/api/exchange/market`);
    const start = await raw('POST', `${guard}/api/demo/session`, { headers: { origin: guard, 'content-type': 'application/json' }, body: '{}' });
    const home = await raw('GET', `${guard}/`);
    assert.equal(market.status, 503);
    assert.equal(start.status, 503);
    assert.ok(!home.text.includes('Launch demo'), 'no demo offered when misconfigured');
    pass('remote mode next to a backend secret fails closed (503, no demo offered)');

    const noTls = `https://localhost:${ports.guard + 1}`;
    await startNext(siteDir, ports.guardWeb + 1, { ...siteEnv, ZWA_PUBLIC_ORIGIN: noTls, NODE_TLS_REJECT_UNAUTHORIZED: '0' });
    await front(tls, ports.guard + 1, ports.guardWeb + 1, 'edge');
    assert.equal((await raw('GET', `${noTls}/api/exchange/market`)).status, 503);
    const untrusted = `https://localhost:${ports.guard + 2}`;
    const { NODE_EXTRA_CA_CERTS: _ca, ...withoutCa } = siteEnv;
    await startNext(siteDir, ports.guardWeb + 2, { ...withoutCa, ZWA_PUBLIC_ORIGIN: untrusted });
    await front(tls, ports.guard + 2, ports.guardWeb + 2, 'edge');
    assert.equal((await raw('GET', `${untrusted}/api/exchange/market`)).status, 502, 'an untrusted exchange certificate is refused');
    const untrustedStart = await raw('POST', `${untrusted}/api/demo/session`, { headers: { origin: untrusted, 'content-type': 'application/json' }, body: '{}' });
    assert.equal(untrustedStart.status, 503);
    pass('exchange TLS is verified: disabling verification fails closed (503); an untrusted certificate gets no data (502/503)');
  }

  // 1. Read-only: the public site and live inventory, before any session.
  const listing = (await raw('GET', `${exchange}/api/exchange/market`)).json;
  {
    const home = await raw('GET', `${site}/`);
    assert.equal(home.status, 200);
    assert.ok(home.text.includes('Launch demo'));
    assert.equal(home.headers['set-cookie'], undefined, 'no cookie for an anonymous visit');
    assert.match(home.headers['content-security-policy'], /connect-src 'self'/);
    const market = await raw('GET', `${site}/api/exchange/market`);
    assert.equal(market.status, 200);
    assert.equal(market.headers['cache-control'], 'no-store');
    assert.deepEqual(market.json.instruments.map((i) => [i.instrumentId, i.availableUnits]),
                     listing.instruments.map((i) => [i.instrumentId, i.availableUnits]));
    const page = await raw('GET', `${site}/exchange`);
    for (const offer of listing.instruments) assert.ok(page.text.includes(`${offer.availableUnits.toLocaleString('en-US')} units`));
    pass('anonymous visit: landing and /exchange render live inventory read through the proxy, no cookie');
  }

  // 2. Proxy security, without a browser.
  {
    const json = { 'content-type': 'application/json' };
    const reject = async (name, request, status) => { const r = await request; assert.equal(r.status, status, `${name}: ${r.status} ${r.text.slice(0, 120)}`); return r; };
    await reject('no Origin', raw('POST', `${site}/api/demo/session`, { headers: json, body: '{}' }), 403);
    await reject('foreign Origin', raw('POST', `${site}/api/demo/session`, { headers: { ...json, origin: 'https://evil.example' }, body: '{}' }), 403);
    await reject('the exchange Origin', raw('POST', `${site}/api/demo/session`, { headers: { ...json, origin: exchange }, body: '{}' }), 403);
    for (const origin of ['null', `http://localhost:${ports.site}`, `https://localhost:${ports.site + 1}`, `https://LOCALHOST:${ports.site}`,
                          `${site}/`, `https://localhost:${ports.site}.evil.example`, `https://evil.example/https://localhost:${ports.site}`]) {
      await reject(`Origin ${origin}`, raw('POST', `${site}/api/demo/session`, { headers: { ...json, origin }, body: '{}' }), 403);
    }
    await reject('X-Forwarded-Host does not replace Host', raw('POST', `http://127.0.0.1:${ports.siteWeb}/api/demo/session`,
      { direct: true, headers: { ...json, origin: site, host: 'evil.example', 'x-forwarded-host': `localhost:${ports.site}` }, body: '{}' }), 403);
    await reject('foreign Host', raw('POST', `http://127.0.0.1:${ports.siteWeb}/api/demo/session`,
      { direct: true, headers: { ...json, origin: site, host: 'evil.example' }, body: '{}' }), 403);
    await reject('oversized body', raw('POST', `${site}/api/demo/session`, { headers: { ...json, origin: site }, body: `{"x":"${'a'.repeat(3000)}"}` }), 413);
    await reject('array body', raw('POST', `${site}/api/demo/session`, { headers: { ...json, origin: site }, body: '[]' }), 400);
    for (const [method, route] of [['POST', '/api/exchange/login'], ['POST', '/api/exchange/logout'], ['POST', '/api/exchange/dealer-policy'],
      ['POST', '/api/exchange/offers'], ['POST', `/api/exchange/offers/${'a'.repeat(32)}/price`], ['POST', '/api/exchange/wallet'],
      ['POST', '/api/exchange/wallet/unpair']]) {
      await reject(`operator route ${route}`, raw(method, `${site}${route}`, { headers: { ...json, origin: site }, body: '{}' }), 404);
    }
    const anonymous = await reject('wallet without a session', raw('GET', `${site}/api/exchange/wallet`), 403);
    assert.equal(anonymous.headers['cache-control'], 'no-store');
    await reject('mutation without a session', raw('POST', `${site}/api/exchange/orders`, { headers: { ...json, origin: site, 'x-zwa-csrf': 'a'.repeat(64) }, body: '{}' }), 403);
    await reject('malformed order id', raw('GET', `${site}/api/exchange/orders/nope`), 404);
    const forged = `${Buffer.from(JSON.stringify({ id: 'judge-0123456789abcdef0123', role: 'buyer', kind: 'demo', sid: '1'.repeat(32), exp: Date.now() + 3600000 })).toString('base64url')}.${'a'.repeat(64)}`;
    await reject('forged cookie read', raw('GET', `${site}/api/exchange/wallet`, { headers: { cookie: `zwa_session=${forged}` } }), 403);
    const forgedPage = await raw('GET', `${site}/wallet`, { headers: { cookie: `zwa_session=${forged}` } });
    assert.ok(forgedPage.text.includes('No wallet yet') && !/csrf/.test(forgedPage.text), 'a forged cookie is signed out');
    const leaks = [anonymous, forgedPage].flatMap((r) => Object.keys(r.headers)).filter((h) => /^(server|x-powered-by|set-cookie)$/.test(h));
    assert.deepEqual(leaks, []);
    pass('proxy refuses missing/null/foreign/look-alike Origins, foreign Host, oversize and non-object bodies, operator routes, forged cookies');
  }

  const browser = await chromium.launch({ channel: 'chrome', headless: process.env.ZWA_E2E_HEADED !== '1' });
  try {
    // 3. Judge A: the whole journey, with lost buy and confirm answers and a refresh mid-order.
    const A = await judge(browser, 21);
    await launch(A);
    const cookie = await sessionCookie(A.context);
    assert.ok(cookie, 'session cookie set on the site');
    assert.deepEqual([cookie.domain, cookie.path, cookie.secure, cookie.httpOnly, cookie.sameSite], ['localhost', '/', true, true, 'Strict']);
    assert.deepEqual(await A.context.cookies(exchange), [], 'the browser holds no cookie for the exchange host');
    await shot(A.page, '01-wallet');
    pass('launch demo through the site: Secure, HttpOnly, SameSite=Strict host-only cookie; wallet ready');

    await A.page.goto(`${site}/exchange`, { waitUntil: 'networkidle' });
    await A.page.getByText('Demo wallet ready').waitFor();
    await openAsset(A.page);
    await fault({ loseBuyAnswers: 2 });
    await A.page.getByRole('button', { name: 'Buy · get exact quote' }).click();
    await A.page.waitForURL('**/orders/*', { timeout: 30000 });
    const orderA = orderIdFrom(A.page);
    const buyer = (await apiOrder(A.context, orderA)).buyerId;
    assert.equal((await ledgerOrders(buyer)).length, 1, 'two lost answers, three submissions, one order');
    pass('lost buy answers through the proxy are resent with the same key: exactly one order');

    await A.page.getByText('Review your quote').waitFor({ timeout: 60000 });
    await A.page.reload({ waitUntil: 'networkidle' });
    await A.page.getByText('Review your quote').waitFor({ timeout: 15000 });
    assert.equal(orderIdFrom(A.page), orderA);
    pass('browser refresh on the quote keeps the same order');

    await fault({ loseConfirmAnswers: 1 });
    await confirmQuote(A.page);
    await A.page.getByText(/Status refreshed from the exchange|did not confirm the result/).waitFor({ timeout: 15000 });
    const seen = await watch(A.page, A.context, orderA, (text) => text === 'Settlement verified');
    await shot(A.page, '02-verified');
    pass('lost confirmation is re-read; Settlement verified only with live receipt + durability', seen.join(' → '));

    const receipt = await A.context.request.get(`${site}/api/exchange/orders/${orderA}/receipt`);
    assert.equal(receipt.status(), 200);
    const txid = (await receipt.json()).txid;
    await A.page.getByRole('link', { name: 'Verify this transaction' }).click();
    await A.page.waitForURL(`**/verify?tx=${txid}`);
    const out = A.page.getByRole('link', { name: /Check this transaction on the exchange host/ });
    assert.equal(await out.getAttribute('href'), `${exchange}/verify?tx=${txid}`);
    await A.page.goto(`${site}/portfolio`, { waitUntil: 'networkidle' });
    await A.page.getByText('Settlement verified').first().waitFor();
    pass('receipt API, portfolio and /verify (links to the exchange host for the chain lookup)');

    // 4. Judge B: isolation from A.
    const B = await judge(browser, 22);
    await launch(B);
    assert.notEqual((await sessionCookie(B.context)).value, cookie.value);
    assert.notEqual((await B.context.request.get(`${site}/api/exchange/orders/${orderA}`)).status(), 200, 'B cannot read A\'s order');
    await B.page.goto(`${site}/orders/${orderA}`, { waitUntil: 'networkidle' });
    await B.page.getByText('Page not found.').waitFor();
    await B.page.goto(`${site}/portfolio`, { waitUntil: 'networkidle' });
    await B.page.getByText('No orders yet.').waitFor();
    await B.page.goto(`${site}/wallet`, { waitUntil: 'networkidle' });
    const csrfB = await csrfOf(B.page);
    await A.page.goto(`${site}/wallet`, { waitUntil: 'networkidle' });
    const csrfA = await csrfOf(A.page);
    assert.ok(csrfA && csrfB && csrfA !== csrfB, 'per-session CSRF tokens');
    const crossed = await B.context.request.post(`${site}/api/demo/end`, { headers: { origin: site, 'x-zwa-csrf': csrfA, 'content-type': 'application/json' }, data: {} });
    assert.equal(crossed.status(), 403, 'A\'s token does not work with B\'s session');
    assert.equal((await B.context.request.get(`${site}/api/exchange/wallet`)).status(), 200, 'B is still active');
    pass('session isolation: B cannot see A\'s order or use A\'s CSRF token; tokens differ per session');

    // 4b. The exchange is unreachable: a state change is an unknown outcome (never a refusal); reads fail; nothing leaks.
    front.exchangeDown = true;
    const lost = await B.context.request.post(`${site}/api/exchange/orders`, { headers: { origin: site, 'x-zwa-csrf': csrfB, 'content-type': 'application/json' },
      data: { offerId: listing.instruments[0].offerId, quantity: 1, idempotencyKey: `buy-${crypto.randomUUID()}` } });
    assert.equal(lost.status(), 503);
    assert.equal((await lost.json()).outcome, 'unknown');
    assert.equal((await B.context.request.get(`${site}/api/exchange/wallet`)).status(), 502);
    const offline = await B.context.request.get(`${site}/wallet`);
    assert.equal(offline.status(), 200, 'pages still render');
    front.exchangeDown = false;
    assert.equal((await B.context.request.get(`${site}/api/exchange/wallet`)).status(), 200, 'B resumes after the outage');
    assert.equal((await B.context.request.get(`${site}/api/exchange/orders`).then((r) => r.json())).orders.length, 0, 'the lost buy never reached the exchange');
    pass('exchange unreachable: a buy is reported as unknown outcome (503), reads 502, pages render; session resumes');

    // 4c. The exchange hangs: the site gives up before its function limit and reports an unknown outcome.
    front.hang = true;
    const t0 = Date.now();
    const hung = await B.context.request.post(`${site}/api/exchange/orders`, { timeout: 60000, headers: { origin: site, 'x-zwa-csrf': csrfB, 'content-type': 'application/json' },
      data: { offerId: listing.instruments[0].offerId, quantity: 1, idempotencyKey: `buy-${crypto.randomUUID()}` } });
    const waited = Date.now() - t0;
    front.hang = false;
    front.hung.splice(0).forEach((req) => req.socket.destroy());
    assert.equal(hung.status(), 503);
    assert.equal((await hung.json()).outcome, 'unknown');
    assert.ok(waited >= 27000 && waited < 30000, `gave up after ${waited} ms (limit 28 s, function maximum 30 s)`);
    pass('exchange hangs: a buy is an unknown outcome after the 28 s upstream timeout, inside the 30 s function limit', `${waited} ms`);

    // 4d. The exchange commits the buy but its answer is lost twice on the way back: same key, one order.
    await openAsset(B.page);
    front.dropResponses = { route: 'POST /api/exchange/orders', count: 2 };
    await B.page.getByRole('button', { name: 'Buy · get exact quote' }).click();
    await B.page.waitForURL('**/orders/*', { timeout: 60000 });
    assert.equal(front.dropResponses.count, 0, 'both answers were dropped');
    const orderB = orderIdFrom(B.page);
    const rowsB = await ledgerOrders((await apiOrder(B.context, orderB)).buyerId);
    assert.equal(rowsB.length, 1, 'committed twice-lost answers, three submissions, exactly one order');
    await B.page.getByText('Review your quote').waitFor({ timeout: 60000 });
    await B.page.getByRole('button', { name: 'Cancel', exact: true }).click();
    await B.page.locator('h1.big-state', { hasText: 'Cancelled' }).waitFor({ timeout: 15000 });
    pass('buy committed upstream with the answer lost twice between exchange and site: same key, exactly one order (then cancelled)');

    // 5. Judge C: an outage on every buy attempt; the unresolved intent survives reload; resume reuses the key.
    const C = await judge(browser, 23);
    await launch(C);
    await openAsset(C.page);
    await fault({ portalDown: 3 });
    await C.page.getByRole('button', { name: 'Buy · get exact quote' }).click();
    await C.page.getByText('We could not confirm whether the exchange accepted your purchase').waitFor({ timeout: 45000 });
    const stored = await C.page.evaluate(() => Object.entries(localStorage).filter(([k]) => k.startsWith('zwa.purchase')).map(([, v]) => JSON.parse(v)));
    assert.equal(stored.length, 1);
    await C.page.reload({ waitUntil: 'networkidle' });
    await C.page.getByText('We could not confirm whether the exchange accepted your purchase').waitFor({ timeout: 15000 });
    await C.page.getByRole('button', { name: 'Check again (same key)' }).click();
    await C.page.waitForURL('**/orders/*', { timeout: 30000 });
    const rows = await ledgerOrders((await apiOrder(C.context, orderIdFrom(C.page))).buyerId);
    assert.equal(rows.length, 1);
    assert.equal(rows[0].idempotency_key, stored[0].key);
    await C.page.getByText('Review your quote').waitFor({ timeout: 60000 });
    await C.page.getByRole('button', { name: 'Cancel', exact: true }).click();
    await C.page.locator('h1.big-state', { hasText: 'Cancelled' }).waitFor({ timeout: 15000 });
    pass('outage: unresolved purchase survives reload; resume uses the original key; one order, then cancelled');

    // 6. Ending a session: the cookie is cleared; a replayed old cookie cannot act.
    await A.page.goto(`${site}/wallet`, { waitUntil: 'networkidle' });
    await A.page.getByRole('button', { name: 'End session and delete wallet' }).click();
    await A.page.waitForURL(`${site}/`, { timeout: 30000 });
    assert.equal(await sessionCookie(A.context), undefined, 'cookie cleared on end');
    const replay = await raw('POST', `${site}/api/exchange/orders`, { headers: { cookie: `zwa_session=${cookie.value}`, origin: site,
      'x-zwa-csrf': csrfA, 'content-type': 'application/json' }, body: JSON.stringify({ offerId: listing.instruments[0].offerId, quantity: 1, idempotencyKey: `buy-${crypto.randomUUID()}` }) });
    assert.notEqual(replay.status, 200, `replayed ended session must not buy: ${replay.status}`);
    const expired = `${Buffer.from(JSON.stringify({ id: buyer, role: 'buyer', kind: 'demo', sid: '2'.repeat(32), exp: Date.now() - 1000 })).toString('base64url')}.${'b'.repeat(64)}`;
    const before = front.exchangeRequests.length;
    const expiredPage = await raw('GET', `${site}/wallet`, { headers: { cookie: `zwa_session=${expired}` } });
    assert.ok(expiredPage.text.includes('No wallet yet'));
    assert.ok(!front.exchangeRequests.slice(before).includes('GET /wallet'), 'an expired cookie is not even sent upstream');
    pass('end session clears the cookie; a replayed ended session cannot buy (' + replay.status + '); an expired cookie is signed out');

    // 7. Every browser request stayed on the site.
    for (const j of [A, B, C]) assert.deepEqual([...j.hosts], [`localhost:${ports.site}`]);
    pass('every browser request went to the site origin only (no direct exchange calls)');

    // 8. The exchange sees every judge as the site's address, so its per-client caps are shared by all judges.
    //    The first refusal is the exchange web's burst bucket (3, then 1 a minute); after a minute the ledger's
    //    cap (3 wallets per client address per hour) answers. Both are recorded as findings, not failures.
    const D = await judge(browser, 24);
    await D.page.goto(site, { waitUntil: 'networkidle' });
    const attempt = async () => {
      const before = front.exchangeRequests.length;
      const r = await D.context.request.post(`${site}/api/demo/session`, { headers: { origin: site, 'content-type': 'application/json' }, data: {} });
      const forwarded = front.exchangeRequests.slice(before).includes('POST /api/demo/session');
      return { status: r.status(), error: (await r.json()).error, forwarded };
    };
    const which = (error) => /from this connection/.test(error) ? 'ledger: 3 wallets per client address per hour'
      : /were started/.test(error) ? 'exchange web burst limit per client address: 3, then 1 a minute' : 'unclassified';
    const shared = 'Every judge reaches the exchange from the site\'s address, so these caps are shared by all judges using the site.';
    const first = await attempt();
    if (first.status === 200) finding('a 4th judge was admitted; the exchange per-client caps did not apply');
    else {
      assert.equal(first.forwarded, true, 'the refusal must come from the exchange, not the site\'s own per-judge bucket');
      finding(`4th judge, refused by the exchange: ${first.status} "${first.error}" (${which(first.error)}). ${shared}`);
      if (which(first.error) !== 'ledger: 3 wallets per client address per hour') {
        await sleep(65000);
        const second = await attempt();
        assert.equal(second.forwarded, true);
        finding(`4th judge after 65 s, refused by the exchange: ${second.status} "${second.error}" (${which(second.error)})`);
      }
    }
    // 9. The site's own per-judge limit (3, then 1 a minute) refuses a burst before anything is forwarded.
    const E = await judge(browser, 25);
    const burst = [];
    for (let i = 0; i < 4; i++) {
      const before = front.exchangeRequests.length;
      const r = await E.context.request.post(`${site}/api/demo/session`, { headers: { origin: site, 'content-type': 'application/json' }, data: {} });
      burst.push([r.status(), front.exchangeRequests.slice(before).includes('POST /api/demo/session')]);
    }
    assert.deepEqual(burst.at(-1), [429, false], `the 4th start is refused by the site itself: ${JSON.stringify(burst)}`);
    pass('the site\'s own per-judge burst limit answers 429 without forwarding', JSON.stringify(burst));
    await Promise.all([A, B, C, D, E].map((j) => j.context.close()));
  } finally {
    await browser.close();
  }
  process.stdout.write(`${results.length}/${results.length} checks passed (TEST DOUBLES; not real settlement)\n`);
  for (const text of findings) process.stdout.write(`FINDING ${text}\n`);
}

const stop = () => { children.forEach((c) => c.kill('SIGINT')); servers.forEach((s) => s.close()); fs.rmSync(tmp, { recursive: true, force: true }); };
main().then(() => { stop(); process.exit(0); },
  (error) => { process.stderr.write(`FAIL ${error.stack || error}\n`); stop(); process.exit(1); });
