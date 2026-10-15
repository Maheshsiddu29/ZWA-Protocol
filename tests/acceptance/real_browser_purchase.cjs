#!/usr/bin/env node
/** REAL browser purchase acceptance: exactly ONE disposable regtest purchase through the judge website.
 *
 *   ZWA_REAL_PURCHASE=ONE \
 *   ZWA_REAL_BASE=https://localhost:9443 ZWA_REAL_PROJECT=zwa-judge-e2e ZWA_REAL_OUT=/private/tmp/zwa-real-e2e \
 *   [ZWA_REAL_CA_FILE=caddy-root.crt] [ZWA_REAL_SSH=host] [ZWA_REAL_SUDO=1] \
 *   ZWA_PLAYWRIGHT_PACKAGE=/path/to/node_modules/playwright node tests/acceptance/real_browser_purchase.cjs
 *
 * 1. Preflight (tests/acceptance/real_settlement_inspect.py): refuses to start unless the project runs the
 *    pinned Zebra, the real engine services, pinned tx-tool, Sapling parameters and the repository's Groth16
 *    verifier, and has no unresolved order. A mocked or test-double component fails here, before any purchase.
 * 2. Installed Google Chrome: Launch demo, wallet READY, verified inventory, ONE Buy click, ONE Confirm click,
 *    live progress. "Settlement verified" must never appear before the API shows a live receipt, reconciliation
 *    and durability. Reload must preserve the result.
 * 3. Independent verification of the order against the ledger and Zebra (inclusion, real v6 transaction,
 *    proofs and signer attestations, durability, reconciliation, one order, no reservations).
 * PASS only if all three pass. Any unknown, failed or uncertain state stops the run with no retry and no
 * second purchase; the environment is left untouched for review.
 *
 * TLS: with ZWA_REAL_CA_FILE (a local Caddy CA's PUBLIC root), Node first validates the served chain against
 * that root including the hostname; only then are that chain's leaf and intermediate keys pinned for this
 * temporary Chrome profile. The system trust store is never changed and Playwright's blanket
 * ignoreHTTPSErrors stays off. Without it (a public certificate), Chrome's normal trust applies.
 * Evidence (screenshots, JSON) goes to ZWA_REAL_OUT, which must be outside the repository. Cookies, CSRF tokens
 * and idempotency keys are never recorded.
 */
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const tls = require('node:tls');
const { execFileSync } = require('node:child_process');

const repo = path.resolve(__dirname, '..', '..');
const env = process.env;
const fail = (message) => { process.stderr.write(`REFUSED ${message}\n`); process.exit(2); };
let url, project, out, instrument, evidence;

function configure() {
  if (env.ZWA_REAL_PURCHASE !== 'ONE') fail('set ZWA_REAL_PURCHASE=ONE to make exactly one real regtest purchase');
  url = (() => { try { return new URL(env.ZWA_REAL_BASE || ''); } catch { return null; } })();
  if (!url || url.protocol !== 'https:' || url.pathname !== '/' || url.search) fail('ZWA_REAL_BASE must be an https origin');
  project = env.ZWA_REAL_PROJECT || '';
  if (!/^[a-z0-9][a-z0-9_-]{0,62}$/.test(project)) fail('ZWA_REAL_PROJECT must name the Compose project to inspect');
  out = path.resolve(env.ZWA_REAL_OUT || '');
  if (!env.ZWA_REAL_OUT || out === repo || out.startsWith(repo + path.sep)) fail('ZWA_REAL_OUT must be outside the repository');
  instrument = env.ZWA_REAL_INSTRUMENT || 'zwa-credit-participation';
  fs.mkdirSync(out, { recursive: true, mode: 0o700 });
  evidence = { base: url.origin, project, instrument, quantity: 1, startedAt: new Date().toISOString(), mutations: [], headlines: [] };
}

const log = [];
const note = (msg) => { const line = `${new Date().toISOString()} ${msg}`; log.push(line); process.stdout.write(line + '\n'); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const scrub = (text) => String(text).split('\n')[0].replace(/zwa_session=\S+/g, 'zwa_session=<redacted>').slice(0, 300);
class Stop extends Error {}

function inspector(...args) {
  const extra = [...(env.ZWA_REAL_SSH ? ['--ssh', env.ZWA_REAL_SSH] : []), ...(env.ZWA_REAL_SUDO === '1' ? ['--sudo'] : [])];
  let text;
  try {
    text = execFileSync('python3', [path.join(repo, 'tests/acceptance/real_settlement_inspect.py'), '--project', project,
      ...extra, ...args], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'], timeout: 600000 });
  } catch (error) { text = error.stdout || ''; }
  try { return JSON.parse(text); } catch { return { ok: false, checks: [{ name: 'inspector output', ok: false, detail: scrub(text) }] }; }
}

/** Validate the served chain against the given root (with hostname), then return its keys' SPKI pins. */
function pinsFromValidatedChain(caFile, origin) {
  return new Promise((resolve, reject) => {
    const socket = tls.connect({ host: origin.hostname, port: Number(origin.port || 443), servername: origin.hostname,
      ca: fs.readFileSync(caFile), rejectUnauthorized: true }, () => {
      const pins = [];
      let cert = socket.getPeerCertificate(true);
      const seen = new Set();
      while (cert && cert.raw && !seen.has(cert.fingerprint256)) {
        seen.add(cert.fingerprint256);
        // Chrome pins SHA-256 of the DER SubjectPublicKeyInfo (not the raw key in cert.pubkey).
        const spki = new crypto.X509Certificate(cert.raw).publicKey.export({ type: 'spki', format: 'der' });
        pins.push(crypto.createHash('sha256').update(spki).digest('base64'));
        cert = cert.issuerCertificate;
      }
      socket.end();
      resolve(pins);
    });
    socket.on('error', (error) => reject(new Stop(`TLS validation against ${path.basename(caFile)} failed: ${error.code || error.message}`)));
  });
}

async function shot(page, name) { await page.screenshot({ path: path.join(out, `${name}.png`) }); note(`screenshot ${name}`); }

async function main() {
  configure();
  const { chromium } = require(env.ZWA_PLAYWRIGHT_PACKAGE || 'playwright');
  let browser;
  try {
    note('preflight: real components and a quiet exchange');
    const before = inspector('preflight');
    fs.writeFileSync(path.join(out, 'preflight.json'), JSON.stringify(before, null, 2));
    if (!before.ok) throw new Stop(`preflight failed: ${before.checks.filter((c) => !c.ok).map((c) => c.name).join('; ')}`);
    note(`preflight passed (${before.checks.length} checks); chain tip ${before.facts.chain?.tip}`);

    const args = [];
    if (env.ZWA_REAL_CA_FILE) {
      const pins = await pinsFromValidatedChain(env.ZWA_REAL_CA_FILE, url);
      args.push(`--ignore-certificate-errors-spki-list=${pins.join(',')}`);
      evidence.tls = { mode: 'local CA validated by Node (hostname checked), served chain keys pinned in a temporary profile', pins: pins.length };
    } else evidence.tls = { mode: 'system trust (public certificate)' };
    browser = await chromium.launch({ channel: 'chrome', headless: env.ZWA_REAL_HEADED !== '1', args });
    const context = await browser.newContext({ ignoreHTTPSErrors: false, viewport: { width: 1280, height: 900 } });
    const page = await context.newPage();
    page.on('response', (response) => {
      if (response.request().method() !== 'POST') return;
      const p = new URL(response.url()).pathname.replace(/[0-9a-f]{32}/, ':id');
      evidence.mutations.push({ at: new Date().toISOString(), path: p, status: response.status() });
      note(`POST ${p} -> ${response.status()}`);
    });
    // Reads use the page's own fetch (Chrome's TLS and cookies); nothing sensitive is recorded.
    const api = (p) => page.evaluate(async (target) => {
      const r = await fetch(target, { cache: 'no-store', credentials: 'same-origin' });
      return { status: r.status, body: await r.json().catch(() => null) };
    }, p);

    await page.goto(url.origin, { waitUntil: 'networkidle' });
    await shot(page, '01-home');
    await page.getByRole('button', { name: 'Launch demo' }).first().click();
    await page.waitForURL('**/wallet', { timeout: 60000 });
    note('demo session created; waiting for wallet READY');
    await page.getByRole('link', { name: 'Browse assets' }).waitFor({ timeout: 600000 });
    const wallet = await api('/api/exchange/wallet');
    if (wallet.status !== 200 || wallet.body.state !== 'READY') throw new Stop(`wallet not READY (${wallet.status})`);
    evidence.wallet = { state: wallet.body.state, nativeZat: wallet.body.nativeZat, scanHeight: wallet.body.scanHeight };
    await shot(page, '02-wallet-ready');

    await page.goto(`${url.origin}/exchange`, { waitUntil: 'networkidle' });
    await page.getByText('Demo wallet ready').waitFor({ timeout: 30000 });
    const market = await api('/api/exchange/market');
    const offer = market.body?.instruments?.find((o) => o.instrumentId === instrument);
    if (!market.body?.chainActive || !offer || !offer.inventoryVerifiedAtTip || offer.availableUnits < 1 || !offer.priceZatPerUnit) {
      throw new Stop('no verified, priced inventory');
    }
    evidence.offer = { availableUnits: offer.availableUnits, priceZatPerUnit: offer.priceZatPerUnit, scanHeight: offer.inventoryScanHeight };
    await shot(page, '03-markets');

    await page.goto(`${url.origin}/exchange/${instrument}`, { waitUntil: 'networkidle' });
    await page.getByText('Network: Zcash regtest').waitFor();
    if (await page.locator('#quantity').inputValue() !== '1') throw new Stop('quantity is not 1');
    await shot(page, '04-asset');
    note('clicking Buy (the only purchase)');
    await page.getByRole('button', { name: 'Buy · get exact quote' }).click();
    try { await page.waitForURL('**/orders/*', { timeout: 120000 }); }
    catch { throw new Stop('buy did not reach an order page; NOT retrying'); }
    const id = page.url().match(/\/orders\/([0-9a-f]{32})/)[1];
    evidence.orderId = id;
    const buys = evidence.mutations.filter((m) => m.path === '/api/exchange/orders');
    if (buys.length !== 1 || buys[0].status !== 200) throw new Stop(`buy requests: ${JSON.stringify(buys)}`);

    await page.getByText('Review your quote').waitFor({ timeout: 600000 });
    const quoted = (await api(`/api/exchange/orders/${id}`)).body;
    if (quoted.quote.quantity !== 1 || quoted.quote.network !== 'regtest') throw new Stop('unexpected quote terms');
    evidence.quote = { paymentZat: quoted.quote.paymentZat, networkFeeZat: quoted.quote.networkFeeZat, network: quoted.quote.network };
    await shot(page, '05-quote');
    await page.getByLabel('I confirm these exact terms.').check();
    note('clicking Confirm purchase (once)');
    await page.getByRole('button', { name: 'Confirm purchase' }).click();

    const end = Date.now() + 30 * 60000;
    let last = '';
    while (true) {
      if (Date.now() > end) throw new Stop('settlement did not finish within 30 minutes');
      const headline = await page.locator('h1.big-state').innerText();
      const order = (await api(`/api/exchange/orders/${id}`)).body;
      const shown = headline === 'Settlement verified' || await page.getByText('Settlement verified', { exact: true }).count() > 0;
      if (shown && !(order.state === 'RECEIPT_AVAILABLE' && order.receipt?.durability && order.effects && order.chainActive && !order.settlementDropped)) {
        await shot(page, 'STOP-premature-verified');
        throw new Stop(`"Settlement verified" shown while the API state was ${order.state}`);
      }
      if (headline !== last || evidence.headlines.at(-1)?.state !== order.state) {
        evidence.headlines.push({ at: new Date().toISOString(), headline, state: order.state });
        note(`headline "${headline}" | API ${order.state}`);
        await shot(page, `06-progress-${String(evidence.headlines.length).padStart(2, '0')}-${order.state}`);
        last = headline;
      }
      if (['CANCELLED', 'EXPIRED', 'REJECTED', 'EXECUTION_FAILED', 'UNCERTAIN'].includes(order.state)) {
        evidence.failure = order.failure;
        throw new Stop(`order ended ${order.state}; NOT retrying`);
      }
      if (shown) { evidence.final = { txid: order.receipt.txid, height: order.receipt.height, confirmations: order.receipt.confirmations,
        durability: order.receipt.durability, effects: order.effects }; break; }
      await sleep(1000);
    }
    await page.getByText('Verified on chain', { exact: true }).waitFor();
    await shot(page, '07-settlement-verified');
    await page.reload({ waitUntil: 'networkidle' });
    const reloaded = await page.locator('h1.big-state').innerText();
    const again = (await api(`/api/exchange/orders/${id}`)).body;
    evidence.reload = { headline: reloaded, state: again.state, txid: again.receipt?.txid };
    if (reloaded !== 'Settlement verified' || again.receipt?.txid !== evidence.final.txid) throw new Stop('reload changed the result');
    await shot(page, '08-after-reload');
    evidence.buyerOrders = ((await api('/api/exchange/orders')).body?.orders ?? []).length;
    if (evidence.buyerOrders !== 1) throw new Stop(`buyer sees ${evidence.buyerOrders} orders`);
    const mutations = evidence.mutations.map((m) => m.path);
    if (JSON.stringify(mutations) !== JSON.stringify(['/api/demo/session', '/api/exchange/orders', '/api/exchange/orders/:id/confirm'])) {
      throw new Stop(`unexpected state-changing requests: ${mutations.join(', ')}`);
    }

    note('independent verification against the ledger and Zebra');
    const verified = inspector('verify', '--order', id, '--preflight', path.join(out, 'preflight.json'),
      '--instrument', instrument, '--quantity', '1');
    fs.writeFileSync(path.join(out, 'verify.json'), JSON.stringify(verified, null, 2));
    if (!verified.ok) throw new Stop(`independent verification failed: ${verified.checks.filter((c) => !c.ok).map((c) => c.name).join('; ')}`);
    if (verified.facts.transaction.txid !== evidence.final.txid) throw new Stop('website and ledger disagree on the txid');
    evidence.verification = verified.facts;
    evidence.result = 'PASS';
  } catch (error) {
    evidence.result = error instanceof Stop ? 'STOPPED' : 'ERROR';
    evidence.stopReason = scrub(error.message || error);
    note(`${evidence.result}: ${evidence.stopReason}`);
  } finally {
    evidence.finishedAt = new Date().toISOString();
    fs.writeFileSync(path.join(out, 'browser-evidence.json'), JSON.stringify(evidence, null, 2));
    fs.writeFileSync(path.join(out, 'browser.log'), log.join('\n') + '\n');
    if (browser) await browser.close();
  }
  note(`RESULT ${evidence.result}`);
  process.exit(evidence.result === 'PASS' ? 0 : 1);
}

if (require.main === module) main();
module.exports = { pinsFromValidatedChain };
