#!/usr/bin/env node
/** READ-ONLY check of a remote-mode site against a REAL hosted exchange, before any deployment or purchase.
 *
 *   (cd website && npm run build)
 *   ZWA_REAL_EXCHANGE=https://<exchange-host> node tests/acceptance/vercel_remote_readonly.cjs
 *
 * Starts this repo's website locally in remote mode (no secrets) behind a loopback TLS edge, pointed at the
 * real exchange, and checks what a judge would see before launching a demo. It never starts a session and
 * never sends a state change upstream: every POST below is refused by the site itself, and the script
 * fails if any request other than GET reaches the exchange. With ZWA_REAL_SITE=https://<deployed-site> it
 * probes an already deployed site instead (same read-only rules, no local server).
 */
const assert = require('node:assert/strict');
const { execFileSync, spawn } = require('node:child_process');
const fs = require('node:fs');
const http = require('node:http');
const https = require('node:https');
const os = require('node:os');
const path = require('node:path');

const exchange = process.env.ZWA_REAL_EXCHANGE || '';
const deployed = process.env.ZWA_REAL_SITE || '';
if (!/^https:\/\/[a-z0-9.-]+$/.test(exchange)) { process.stderr.write('set ZWA_REAL_EXCHANGE=https://<exchange-host>\n'); process.exit(2); }
const siteDir = path.resolve(__dirname, '..', '..', 'website');
const ports = { web: 3341, site: 4441 };
const site = deployed || `https://localhost:${ports.site}`;
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'zwa-remote-ro-'));
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const results = [];
const pass = (name, detail = '') => { results.push(name); process.stdout.write(`PASS ${name}${detail ? `  ${detail}` : ''}\n`); };
let child, server, agent;

function get(url, headers = {}, method = 'GET', body) {
  const target = new URL(url);
  return new Promise((resolve, reject) => {
    const req = https.request({ host: target.hostname, port: target.port || 443, method, path: target.pathname + target.search,
      headers: { ...headers, ...(body ? { 'content-type': 'application/json', 'content-length': Buffer.byteLength(body) } : {}) },
      agent: target.hostname === 'localhost' ? agent : undefined, timeout: 30000 }, (res) => {
      let text = '';
      res.on('data', (c) => { text += c; });
      res.on('end', () => { let json = null; try { json = JSON.parse(text); } catch { /* html */ } resolve({ status: res.statusCode, headers: res.headers, text, json }); });
    });
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

async function startLocalSite() {
  const key = path.join(tmp, 'key.pem'), cert = path.join(tmp, 'cert.pem');
  execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=localhost',
    '-addext', 'subjectAltName=DNS:localhost', '-keyout', key, '-out', cert], { stdio: 'ignore' });
  agent = new https.Agent({ ca: fs.readFileSync(cert) });
  // Only public configuration. The exchange host uses a publicly trusted certificate.
  child = spawn(path.join(siteDir, 'node_modules/.bin/next'), ['start', '-H', '127.0.0.1', '-p', String(ports.web)], { cwd: siteDir,
    env: { PATH: process.env.PATH, HOME: process.env.HOME, NODE_ENV: 'production', ZWA_EXCHANGE_ORIGIN: exchange, ZWA_PUBLIC_ORIGIN: site, ZWA_TRUST_PROXY: '1' },
    stdio: ['ignore', 'ignore', 'inherit'] });
  server = https.createServer({ key: fs.readFileSync(key), cert: fs.readFileSync(cert) }, (req, res) => {
    const out = http.request({ host: '127.0.0.1', port: ports.web, method: req.method, path: req.url,
      headers: { ...req.headers, 'x-forwarded-for': '203.0.113.9' } }, (reply) => { res.writeHead(reply.statusCode, reply.rawHeaders); reply.pipe(res); });
    out.on('error', () => { res.writeHead(502); res.end(); });
    req.pipe(out);
  });
  await new Promise((resolve) => server.listen(ports.site, '127.0.0.1', resolve));
  for (let i = 0; i < 60; i++) { try { if ((await get(`${site}/privacy`)).status === 200) return; } catch { /* starting */ } await sleep(500); }
  throw new Error('site did not start');
}

// Every upstream request the local site makes is a fetch() in its own process; we cannot see those
// from here, so the guarantee is structural: the POSTs below must be refused by the site (403/404/413/400)
// with the site's own error bodies, which is only possible if they were never forwarded.
async function main() {
  if (!deployed) await startLocalSite();
  const real = await get(`${exchange}/api/exchange/market`);
  assert.equal(real.status, 200, 'the real exchange answers its public market');
  pass('real exchange public market (direct, read-only)', `chain height ${real.json.chainHeight}, ${real.json.instruments.length} listings, dealer ${real.json.demo?.dealerPolicy}`);

  const home = await get(`${site}/`);
  assert.equal(home.status, 200);
  assert.ok(home.text.includes('Launch demo'), 'the demo is offered');
  assert.equal(home.headers['set-cookie'], undefined);
  pass('landing page renders, offers Launch demo, sets no cookie');

  const market = await get(`${site}/api/exchange/market`);
  assert.equal(market.status, 200);
  assert.equal(market.headers['cache-control'], 'no-store');
  assert.deepEqual(market.json.instruments.map((i) => i.offerId).sort(), real.json.instruments.map((i) => i.offerId).sort());
  pass('market through the site equals the exchange listing', market.json.instruments.map((i) => `${i.instrumentId}:${i.availableUnits}@${i.priceZatPerUnit}`).join(' '));

  const page = await get(`${site}/exchange`);
  assert.equal(page.status, 200);
  for (const offer of market.json.instruments) assert.ok(page.text.includes(`${offer.availableUnits.toLocaleString('en-US')} units`), offer.instrumentId);
  const asset = await get(`${site}/exchange/zwa-credit-participation`);
  assert.equal(asset.status, 200);
  assert.ok(asset.text.includes('Launch demo') && !/csrf\\?":\\?"[0-9a-f]{64}/.test(asset.text));
  pass('/exchange and an asset page render live inventory; no session, no CSRF token');

  const wallet = await get(`${site}/api/exchange/wallet`);
  assert.equal(wallet.status, 403);
  const walletPage = await get(`${site}/wallet`);
  assert.ok(walletPage.status === 200 && walletPage.text.includes('No wallet yet'));
  const forged = `${Buffer.from(JSON.stringify({ id: 'judge-0123456789abcdef0123', role: 'buyer', kind: 'demo', sid: '1'.repeat(32), exp: Date.now() + 3600000 })).toString('base64url')}.${'a'.repeat(64)}`;
  const forgedPage = await get(`${site}/wallet`, { cookie: `zwa_session=${forged}` });
  assert.ok(forgedPage.text.includes('No wallet yet'), 'the exchange rejects a forged cookie');
  pass('no session: wallet API 403, wallet page signed out; a forged cookie is rejected by the real exchange');

  const refused = [
    ['POST /api/demo/session without Origin', '/api/demo/session', {}, 403],
    ['POST /api/demo/session from a foreign Origin', '/api/demo/session', { origin: 'https://evil.example' }, 403],
    ['POST /api/exchange/orders without a session', '/api/exchange/orders', { origin: site, 'x-zwa-csrf': 'a'.repeat(64) }, 403],
    ['POST /api/exchange/login (operator)', '/api/exchange/login', { origin: site }, 404],
    ['POST /api/exchange/dealer-policy (operator)', '/api/exchange/dealer-policy', { origin: site }, 404],
  ];
  for (const [name, route, headers, status] of refused) {
    const r = await get(`${site}${route}`, headers, 'POST', '{}');
    assert.equal(r.status, status, `${name}: ${r.status}`);
  }
  pass('state changes without a valid origin or session, and operator routes, are refused by the site itself');

  // Routing and assets: every page answers, and every script, style, font, image and icon it references loads.
  const pages = ['/', '/exchange', ...market.json.instruments.map((i) => `/exchange/${i.instrumentId}`), '/wallet', '/portfolio',
    '/verify', `/verify?tx=${'0'.repeat(64)}`, '/privacy', '/terms', '/sign-in', '/seller'];
  const assets = new Set();
  for (const route of pages) {
    const r = await get(`${site}${route}`);
    assert.equal(r.status, 200, `${route}: ${r.status}`);
    for (const m of r.text.matchAll(/(?:src|href)="(\/(?:_next\/static|brand|illustrations|video|animations|icon)[^"?#]*)/g)) assets.add(m[1]);
    for (const m of r.text.matchAll(/\/_next\/image\?url=[^"\s]+/g)) assets.add(m[0].replace(/&amp;/g, '&'));
  }
  const css = [...assets].filter((a) => a.endsWith('.css'));
  for (const sheet of css) for (const m of (await get(`${site}${sheet}`)).text.matchAll(/url\((\/_next\/static\/[^)]+)\)/g)) assets.add(m[1]);
  const broken = [];
  for (const asset of assets) { const r = await get(`${site}${asset}`); if (r.status !== 200) broken.push(`${asset} ${r.status}`); }
  assert.deepEqual(broken, []);
  const missing = await get(`${site}/no-such-page`);
  assert.equal(missing.status, 404);
  pass(`routing and assets: ${pages.length} pages 200, ${assets.size} referenced assets 200, unknown path 404`);
  process.stdout.write(`${results.length}/${results.length} read-only checks passed against ${exchange} (no session started, no state change sent)\n`);
}

const stop = () => { if (child) child.kill('SIGINT'); if (server) server.close(); fs.rmSync(tmp, { recursive: true, force: true }); };
main().then(() => { stop(); process.exit(0); }, (error) => { process.stderr.write(`FAIL ${error.stack || error}\n`); stop(); process.exit(1); });
