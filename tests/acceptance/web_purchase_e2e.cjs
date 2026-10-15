#!/usr/bin/env node
/** Browser end-to-end: the judge purchase journey and its failure handling, against TEST DOUBLES.
 *
 *   (cd website && npm run build)
 *   ZWA_PLAYWRIGHT_PACKAGE=/path/to/node_modules/playwright node tests/acceptance/web_purchase_e2e.cjs
 *
 * Starts tests/acceptance/web_test_double_exchange.py (real portal, ledger, order service and demo signer;
 * fake tx-tool, verifier and chain) and the built website on loopback, then drives installed Google Chrome.
 * It proves the website's flow, idempotency and honesty rules. It proves NOTHING about real settlement:
 * no transaction, proof or block in this run is real. Optional ZWA_E2E_SHOTS=<dir> saves screenshots.
 */
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const readline = require('node:readline');
const { chromium } = require(process.env.ZWA_PLAYWRIGHT_PACKAGE || 'playwright');

const repo = path.resolve(__dirname, '..', '..');
const port = Number(process.env.ZWA_E2E_PORT || 3317);
const base = `http://127.0.0.1:${port}`;
const shots = process.env.ZWA_E2E_SHOTS || '';
const children = [];
const results = [];
const pass = (name, detail = '') => { results.push(name); process.stdout.write(`PASS ${name}${detail ? `  ${detail}` : ''}\n`); };
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function startBackend() {
  const child = spawn('python3', ['tests/acceptance/web_test_double_exchange.py', '--pace', '0.25'],
    { cwd: repo, stdio: ['ignore', 'pipe', 'inherit'] });
  children.push(child);
  return new Promise((resolve, reject) => {
    readline.createInterface({ input: child.stdout }).once('line', (line) => resolve(JSON.parse(line)));
    child.once('exit', (code) => reject(new Error(`backend exited ${code}`)));
  });
}

async function startWeb(backend) {
  const env = { PATH: process.env.PATH, HOME: process.env.HOME, NODE_ENV: 'production',
    ZWA_DEMO_MODE: '1', ZWA_PORTAL_URL: backend.portalUrl, ZWA_PORTAL_TOKEN: backend.portalToken,
    ZWA_SESSION_SECRET: crypto.randomBytes(32).toString('hex'), ZWA_ROLE_ACCOUNTS: '[]',
    ZWA_PORTFOLIO_SELLER_ID: 'dealer', ZWA_TRUST_PROXY: '1' };
  const child = spawn(path.join(repo, 'website/node_modules/.bin/next'), ['start', '-H', '127.0.0.1', '-p', String(port)],
    { cwd: path.join(repo, 'website'), env, stdio: ['ignore', 'ignore', 'inherit'] });
  children.push(child);
  for (let i = 0; i < 60; i++) {
    try { if ((await fetch(base)).ok) return; } catch { /* starting */ }
    await sleep(500);
  }
  throw new Error('website did not start');
}

let control = '';
async function fault(body) {
  const response = await fetch(`${control}/fault`, { method: 'POST', body: JSON.stringify(body) });
  assert.equal(response.status, 200);
}
async function ledgerOrders(buyerId) {
  const state = await (await fetch(`${control}/state`)).json();
  assert.deepEqual(state.loopErrors, [], 'the exchange loop must not fail');
  return state.orders.filter((o) => o.buyer_id === buyerId);
}

async function shot(page, name) {
  if (!shots) return;
  fs.mkdirSync(shots, { recursive: true });
  await page.screenshot({ path: path.join(shots, `${name}.png`), fullPage: true });
}

/** One judge: its own Chrome context and client address. */
async function launchJudge(browser, n) {
  const context = await browser.newContext({ extraHTTPHeaders: { 'x-forwarded-for': `198.51.100.${n}` } });
  const page = await context.newPage();
  await page.goto(base, { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: 'Launch demo' }).first().click();
  await page.waitForURL('**/wallet', { timeout: 20000 });
  await page.getByRole('link', { name: 'Browse assets' }).waitFor({ timeout: 60000 });
  await page.getByText('Zcash regtest').first().waitFor();
  return { context, page };
}

async function openAsset(page, instrument = 'zwa-gold-reference') {
  await page.goto(`${base}/exchange/${instrument}`, { waitUntil: 'networkidle' });
  await page.getByText('Network: Zcash regtest').waitFor();
}

const orderIdFrom = (page) => page.url().match(/\/orders\/([0-9a-f]{32})/)[1];
const headline = (page) => page.locator('h1.big-state').innerText();
async function apiOrder(context, id) {
  const response = await context.request.get(`${base}/api/exchange/orders/${id}`);
  assert.equal(response.status(), 200);
  return response.json();
}

async function confirmQuote(page) {
  await page.getByText('Review your quote').waitFor({ timeout: 60000 });
  await page.getByLabel('I confirm these exact terms.').check();
  await page.getByRole('button', { name: 'Confirm purchase' }).click();
}

/** Watch the order page until `done`; "Settlement verified" must never lead the API's own verdict. */
async function watch(page, context, id, done, timeout = 120000) {
  const seen = [];
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const text = await headline(page);
    if (seen.at(-1) !== text) seen.push(text);
    if (text === 'Settlement verified' || await page.getByText('Settlement verified').count()) {
      const order = await apiOrder(context, id);
      assert.equal(order.state, 'RECEIPT_AVAILABLE', 'verified shown before RECEIPT_AVAILABLE');
      assert.ok(order.receipt && order.receipt.durability && order.effects && !order.settlementDropped,
        'verified shown without a live receipt, reconciliation and durability');
    }
    if (await done(text)) return seen;
    await sleep(250);
  }
  const last = await apiOrder(context, id);
  throw new Error(`timed out; headlines seen: ${seen.join(' → ')}; API state ${last.state}, failure ${JSON.stringify(last.failure)}`);
}

async function main() {
  const backend = await startBackend();
  assert.equal(backend.testDoubles, true);
  control = backend.control;
  await startWeb(backend);
  const browser = await chromium.launch({ channel: 'chrome', headless: process.env.ZWA_E2E_HEADED !== '1' });
  try {
    // 1. The whole journey: launch, wallet, market, buy once, confirm, progress, verified receipt.
    {
      const { context, page } = await launchJudge(browser, 11);
      await shot(page, '01-wallet-ready');
      await page.goto(`${base}/exchange`, { waitUntil: 'networkidle' });
      await page.getByText('Demo wallet ready').waitFor();
      await shot(page, '02-markets');
      await openAsset(page);
      await page.getByText('You hold', { exact: true }).waitFor();
      await page.getByRole('button', { name: 'Buy · get exact quote' }).click();
      await page.waitForURL('**/orders/*', { timeout: 30000 });
      const id = orderIdFrom(page);
      await page.getByText('Zcash regtest (experimental ZSA test chain)').waitFor({ timeout: 60000 });
      await shot(page, '03-quote');
      await confirmQuote(page);
      const seen = await watch(page, context, id, (text) => text === 'Settlement verified');
      assert.ok(seen.includes('Settlement verified'));
      await page.getByText('Verified on chain', { exact: true }).waitFor();
      const stages = await page.locator('ol.progress li').evaluateAll((items) => items.map((li) => li.className));
      assert.ok(stages.every((c) => c === 'done'), `every stage done: ${stages}`);
      await shot(page, '04-verified');
      const order = await apiOrder(context, id);
      assert.equal((await ledgerOrders(order.buyerId)).length, 1);
      pass('journey: launch → wallet → buy → confirm → Settlement verified only after live receipt + durability',
        seen.join(' → '));
      await page.getByRole('link', { name: 'View portfolio' }).click();
      await page.getByText('Verified settlements').waitFor();
      await page.getByText('Settlement verified').first().waitFor();
      pass('portfolio counts and labels only verified settlements');
      await context.close();
    }

    // 2. Lost buy answers (committed, then the portal's generic error) and a lost confirm answer.
    {
      const { context, page } = await launchJudge(browser, 12);
      await openAsset(page);
      await fault({ loseBuyAnswers: 2 });
      await page.getByRole('button', { name: 'Buy · get exact quote' }).click();
      await page.waitForURL('**/orders/*', { timeout: 30000 });
      const id = orderIdFrom(page);
      const order = await apiOrder(context, id);
      const rows = await ledgerOrders(order.buyerId);
      assert.equal(rows.length, 1, 'two lost answers, three submissions, one order');
      assert.equal(await page.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith('zwa.purchase')).length), 0,
        'a resolved intent is cleared');
      pass('lost buy answers are resent with the same key: exactly one order', rows[0].idempotency_key.slice(0, 8) + '…');
      await fault({ loseConfirmAnswers: 1 });
      await confirmQuote(page);
      await page.getByText(/Status refreshed from the exchange|did not confirm the result/).waitFor({ timeout: 15000 });
      await watch(page, context, id, (text) => text === 'Settlement verified');
      pass('a lost confirmation answer is re-read, not reported as an error, and the order settles');
      await context.close();
    }

    // 3. Outage on every buy attempt: unresolved state survives a reload; resuming reuses the same key.
    {
      const { context, page } = await launchJudge(browser, 13);
      await openAsset(page);
      await fault({ portalDown: 3 });
      await page.getByRole('button', { name: 'Buy · get exact quote' }).click();
      await page.getByText('We could not confirm whether the exchange accepted your purchase').waitFor({ timeout: 30000 });
      const stored = await page.evaluate(() => Object.entries(localStorage).filter(([k]) => k.startsWith('zwa.purchase')).map(([, v]) => JSON.parse(v)));
      assert.equal(stored.length, 1);
      assert.equal(stored[0].unknown, true);
      const wallet = await (await context.request.get(`${base}/api/exchange/wallet`)).json();
      assert.equal(wallet.openOrderId ?? null, null, 'nothing reached the exchange');
      await shot(page, '05-unresolved');
      await page.reload({ waitUntil: 'networkidle' });
      await page.getByText('We could not confirm whether the exchange accepted your purchase').waitFor({ timeout: 15000 });
      assert.equal(await page.getByRole('button', { name: 'Buy · get exact quote' }).count(), 0, 'no new purchase while unresolved');
      pass('unresolved purchase is persisted, survives reload, and blocks a new key');
      await page.getByRole('button', { name: 'Check again (same key)' }).click();
      await page.waitForURL('**/orders/*', { timeout: 30000 });
      const order = await apiOrder(context, orderIdFrom(page));
      const rows = await ledgerOrders(order.buyerId);
      assert.equal(rows.length, 1);
      assert.equal(rows[0].idempotency_key, stored[0].key, 'resumed with the original key');
      pass('resuming creates exactly one order with the original idempotency key');
      await page.getByText('Review your quote').waitFor({ timeout: 60000 });
      await page.getByRole('button', { name: 'Cancel', exact: true }).click();
      await page.locator('h1.big-state', { hasText: 'Cancelled' }).waitFor({ timeout: 15000 });
      pass('an unwanted recovered order is cancelled before anything is signed');
      await context.close();
    }

    // 4. Dealer refusal after the buyer signs: an honest failure, nothing transferred, never verified.
    {
      const { context, page } = await launchJudge(browser, 14);
      await openAsset(page);
      await fault({ dealerPause: true });
      await page.getByRole('button', { name: 'Buy · get exact quote' }).click();
      await page.waitForURL('**/orders/*', { timeout: 30000 });
      const id = orderIdFrom(page);
      await confirmQuote(page);
      const seen = await watch(page, context, id, (text) => ['Not settled', 'Not executed'].includes(text));
      await page.getByText('Nothing was transferred.').waitFor();
      assert.ok(!seen.includes('Settlement verified'));
      await shot(page, '06-refused');
      pass('dealer refusal is shown as not settled with nothing transferred', seen.join(' → '));
      await fetch(`${control}/resume-dealer`, { method: 'POST' });
      await context.close();
    }

    // 5. Coordinator stops after both signatures: unresolved, with recovery guidance; never verified.
    {
      const { context, page } = await launchJudge(browser, 15);
      await openAsset(page);
      await fault({ uncertain: true });
      await page.getByRole('button', { name: 'Buy · get exact quote' }).click();
      await page.waitForURL('**/orders/*', { timeout: 30000 });
      const id = orderIdFrom(page);
      await confirmQuote(page);
      const seen = await watch(page, context, id, (text) => text === 'Held for review');
      await page.getByText('Do not try to buy again').waitFor();
      await page.getByRole('button', { name: 'Check status again' }).waitFor();
      assert.equal((await apiOrder(context, id)).state, 'UNCERTAIN');
      assert.ok(!seen.includes('Settlement verified'));
      await page.setViewportSize({ width: 390, height: 844 });
      await shot(page, '07-uncertain-mobile');
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
      assert.equal(overflow, false, 'no horizontal scroll at phone width');
      pass('uncertain settlement shows Unresolved with safe recovery guidance (also at 390px)', seen.join(' → '));
      await context.close();
    }
  } finally {
    await browser.close();
  }
  process.stdout.write(`${results.length}/${results.length} browser checks passed (TEST DOUBLES; not real settlement)\n`);
}

main().then(() => { children.forEach((c) => c.kill('SIGINT')); process.exit(0); },
  (error) => { process.stderr.write(`FAIL ${error.stack || error}\n`); children.forEach((c) => c.kill('SIGINT')); process.exit(1); });
