#!/usr/bin/env node
/** Two real, isolated Chrome contexts against the loopback HTTPS judge demo.
 *
 * Install Playwright separately, then set ZWA_PLAYWRIGHT_PACKAGE to its module
 * directory if it is not installed in this repository. No TLS overrides.
 * This test creates and retires two disposable wallets but never confirms a
 * purchase or submits a settlement.
 */
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const { chromium } = require(process.env.ZWA_PLAYWRIGHT_PACKAGE || 'playwright');

const base = process.env.ZWA_BROWSER_BASE || 'https://localhost:8443';
if (!/^https:\/\/localhost:(?:8443|9443)$/.test(base)) throw new Error('M10 browser test is loopback-only');
const pass = (name) => process.stdout.write(`PASS ${name}\n`);

async function json(response) {
  try { return await response.json(); } catch { return {}; }
}

async function csrf(page) {
  const html = await page.content();
  const found = html.match(/csrf\\?"\s*:\s*\\?"([0-9a-f]{64})/);
  assert.ok(found, 'rendered page must carry its own CSRF token');
  return found[1];
}

async function start(browser) {
  const context = await browser.newContext({ ignoreHTTPSErrors: false });
  const page = await context.newPage();
  await page.goto(base, { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: 'Try the demo' }).first().click();
  await page.waitForURL('**/wallet', { timeout: 20000 });
  await page.getByRole('link', { name: 'Browse assets' }).waitFor({ timeout: 180000 });
  // A client-side navigation need not leave the RSC payload in page.content().
  // A normal refresh gives us the server-rendered CSRF prop for this session.
  await page.reload({ waitUntil: 'networkidle' });
  const walletResponse = await context.request.get(`${base}/api/exchange/wallet`);
  assert.equal(walletResponse.status(), 200);
  const wallet = await walletResponse.json();
  assert.equal(wallet.state, 'READY');
  assert.ok(wallet.nativeZat > 0);
  const cookies = await context.cookies(base);
  const session = cookies.find(c => c.name === 'zwa_session');
  assert.ok(session, 'session cookie');
  assert.equal(session.secure, true);
  assert.equal(session.httpOnly, true);
  assert.equal(session.sameSite, 'Strict');
  return { context, page, wallet, csrf: await csrf(page) };
}

async function post(visitor, path, body, headers = {}) {
  return visitor.context.request.post(`${base}${path}`, {
    data: body,
    headers: { origin: base, 'x-zwa-csrf': visitor.csrf, ...headers },
  });
}

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  let a, b;
  try {
    a = await start(browser);
    b = await start(browser);
    pass('two browser contexts provision funded wallets over validated HTTPS');
    assert.notEqual(a.wallet.walletAddress, b.wallet.walletAddress);
    assert.notEqual(a.wallet.fundingTxid, b.wallet.fundingTxid);
    pass('wallet identities and funding transactions differ');
    pass('cookies are Secure, HttpOnly and SameSite=Strict');

    const noCsrf = await a.context.request.post(`${base}/api/exchange/orders`, {
      data: { offerId: 'invalid', quantity: 1, idempotencyKey: 'missing-csrf' },
      headers: { origin: base },
    });
    assert.equal(noCsrf.status(), 403);
    pass('missing CSRF is rejected');
    const foreignOrigin = await post(a, '/api/exchange/orders', {}, { origin: 'https://evil.example' });
    assert.equal(foreignOrigin.status(), 403);
    pass('foreign Origin is rejected');
    try {
      const foreignHost = await post(a, '/api/exchange/orders', {}, { host: 'evil.example' });
      assert.notEqual(foreignHost.status(), 200);
    } catch (error) {
      // Caddy may reject the mismatched TLS SNI/Host pair before HTTP routing.
      assert.match(String(error.message || error), /EPROTO|ERR_SSL|certificate|tls/i);
    }
    pass('CSRF, Origin and Host checks reject forged mutations');

    const ownWallet = await a.context.request.get(`${base}/api/exchange/wallet?walletId=${encodeURIComponent(b.wallet.walletAddress)}`);
    assert.equal(ownWallet.status(), 200);
    assert.equal((await ownWallet.json()).walletAddress, a.wallet.walletAddress);
    const otherWallet = await b.context.request.get(`${base}/api/exchange/wallet?walletId=${encodeURIComponent(a.wallet.walletAddress)}`);
    assert.equal((await otherWallet.json()).walletAddress, b.wallet.walletAddress);
    pass('browser-supplied wallet identity cannot switch sessions');

    const market = await json(await a.context.request.get(`${base}/api/exchange/market`));
    const gold = market.instruments.find(item => item.instrumentId === 'zwa-gold-reference');
    assert.ok(gold?.offerId && gold.availableUnits > 0);
    const key = `m10-${crypto.randomUUID()}`;
    const created = await post(a, '/api/exchange/orders', {
      offerId: gold.offerId, quantity: 1, idempotencyKey: key,
      walletAddress: b.wallet.walletAddress, buyerId: 'judge-00000000000000000000',
    });
    assert.equal(created.status(), 200);
    const order = await created.json();
    assert.notEqual(order.buyerId, 'judge-00000000000000000000');
    const retry = await post(a, '/api/exchange/orders', { offerId: gold.offerId, quantity: 1, idempotencyKey: key });
    assert.equal((await retry.json()).id, order.id);
    pass('forged buyer fields ignored and order creation idempotent');

    assert.notEqual((await b.context.request.get(`${base}/api/exchange/orders/${order.id}`)).status(), 200);
    assert.notEqual((await post(b, `/api/exchange/orders/${order.id}/cancel`, {})).status(), 200);
    assert.notEqual((await post(a, '/api/exchange/dealer-policy', { policy: 'PAUSED' })).status(), 200);
    pass('other session cannot read or cancel order; buyer cannot administer dealer');

    const deadline = Date.now() + 90000;
    let detail;
    do {
      detail = await json(await a.context.request.get(`${base}/api/exchange/orders/${order.id}`));
      if (detail.state === 'QUOTE_READY') break;
      await new Promise(resolve => setTimeout(resolve, 1000));
    } while (Date.now() < deadline);
    assert.equal(detail.state, 'QUOTE_READY');
    const badQuote = await post(a, `/api/exchange/orders/${order.id}/confirm`, { quoteDigest: '0'.repeat(64) });
    assert.equal(badQuote.status(), 409);
    pass('invalid quote digest fails closed before signing');
    assert.equal((await post(a, `/api/exchange/orders/${order.id}/cancel`, {})).status(), 200);

    for (const path of ['/v1/portal', '/rpc', '/api/sign']) {
      const response = await a.context.request.post(`${base}${path}`, { data: {} });
      assert.ok([404, 405].includes(response.status()), `${path}: ${response.status()}`);
    }
    pass('proxy does not expose portal, Zebra RPC or signing routes');

    const html = await a.page.content();
    assert.ok(!/ZWA_SESSION_SECRET=|ZWA_PORTAL_TOKEN=|BEGIN (?:RSA |EC )?PRIVATE KEY/.test(html));
    pass('rendered wallet page has no obvious secret material');

    for (const visitor of [a, b]) {
      const ended = await post(visitor, '/api/demo/end', {});
      assert.equal(ended.status(), 200);
      const denied = await visitor.context.request.get(`${base}/api/exchange/wallet`);
      assert.notEqual(denied.status(), 200);
    }
    pass('ended sessions lose wallet authorization');
  } finally {
    for (const visitor of [a, b]) {
      if (visitor) {
        try { await post(visitor, '/api/demo/end', {}); } catch { /* already ended or unavailable */ }
      }
    }
    await a?.context.close();
    await b?.context.close();
    await browser.close();
  }
})().catch(error => {
  // Playwright's request errors can include a Cookie header in their call log.
  console.error(`FAIL ${String(error.message || error).split('\n')[0]}`);
  process.exitCode = 1;
});
