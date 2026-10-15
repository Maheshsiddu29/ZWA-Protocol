// Remote-exchange mode: configuration, session discovery and cookie rules. Run: npm test
import assert from "node:assert/strict";
import test from "node:test";
import { cookieClaim, demoSessionFromWalletPage, flightText, httpsOrigin, remoteConfig, sessionCookieChange,
         validSessionCookie } from "../lib/remote-contract.ts";

const SITE = "https://zwaprotocol.vercel.app", EXCHANGE = "https://exchange.example.org";
const CSRF = "c".repeat(64), OTHER = "d".repeat(64), ID = "judge-0123456789abcdef0123";

test("remote mode needs two distinct exact https origins", () => {
  assert.deepEqual(remoteConfig({}), { state: "off" });
  assert.deepEqual(remoteConfig({ ZWA_EXCHANGE_ORIGIN: EXCHANGE, ZWA_PUBLIC_ORIGIN: SITE }),
    { state: "on", exchange: EXCHANGE, site: SITE, siteHost: "zwaprotocol.vercel.app" });
  assert.equal(remoteConfig({ ZWA_EXCHANGE_ORIGIN: `${EXCHANGE}/`, ZWA_PUBLIC_ORIGIN: `${SITE}/` }).state, "on");
  for (const bad of ["http://exchange.example.org", `${EXCHANGE}/api`, `${EXCHANGE}?x=1`, "https://user@exchange.example.org",
                     "https://*.example.org", "exchange.example.org", "https://EXCHANGE.example.org"]) {
    assert.equal(remoteConfig({ ZWA_EXCHANGE_ORIGIN: bad, ZWA_PUBLIC_ORIGIN: SITE }).state, "invalid", bad);
  }
  assert.equal(remoteConfig({ ZWA_EXCHANGE_ORIGIN: EXCHANGE }).state, "invalid");
  assert.equal(remoteConfig({ ZWA_EXCHANGE_ORIGIN: EXCHANGE, ZWA_PUBLIC_ORIGIN: "http://localhost:3000" }).state, "invalid");
  assert.equal(remoteConfig({ ZWA_EXCHANGE_ORIGIN: SITE, ZWA_PUBLIC_ORIGIN: SITE }).state, "invalid");
  assert.equal(httpsOrigin("https://a.example:8443")?.origin, "https://a.example:8443");
  assert.equal(httpsOrigin(""), null);
});

test("remote mode refuses to run next to any backend secret", () => {
  for (const name of ["ZWA_SESSION_SECRET", "ZWA_ROLE_ACCOUNTS", "ZWA_PORTAL_URL", "ZWA_PORTAL_TOKEN", "ZWA_PRIVATE_EXCHANGE_ROOT"]) {
    const config = remoteConfig({ ZWA_EXCHANGE_ORIGIN: EXCHANGE, ZWA_PUBLIC_ORIGIN: SITE, [name]: "x" });
    assert.equal(config.state, "invalid", name);
    assert.match(config.reason, new RegExp(name));
  }
  assert.equal(remoteConfig({ ZWA_EXCHANGE_ORIGIN: EXCHANGE, ZWA_PUBLIC_ORIGIN: SITE, ZWA_SESSION_SECRET: "" }).state, "on");
  assert.equal(remoteConfig({ ZWA_EXCHANGE_ORIGIN: EXCHANGE, ZWA_PUBLIC_ORIGIN: SITE, NODE_TLS_REJECT_UNAUTHORIZED: "0" }).state, "invalid",
    "the exchange certificate must be verified");
});

// A page whose RSC payload is split across two inline chunks, as Next.js may stream it.
function walletPage(flight, split = 40) {
  const push = (part) => `<script>self.__next_f.push([1,${JSON.stringify(part)}])</script>`;
  return `<html><body>${push(flight.slice(0, split))}${push(flight.slice(split))}</body></html>`;
}
const viewer = (kind = "demo", role = "buyer", id = ID) => `{"viewer":{"kind":"${kind}","role":"${role}","id":"${id}"},"demo":true}`;
const wallet = (csrf = CSRF) => `["$","$L14",null,{"csrf":"${csrf}","initial":{"state":"READY"}}]`;

test("the demo session is read from the exchange's own wallet page", () => {
  const html = walletPage(`1:${viewer()}\n2:${wallet()}\n`);
  assert.equal(flightText(html), `1:${viewer()}\n2:${wallet()}\n`);
  assert.deepEqual(demoSessionFromWalletPage(html), { id: ID, csrf: CSRF });
  for (const split of [5, 60, 120]) assert.deepEqual(demoSessionFromWalletPage(walletPage(`1:${viewer()}\n2:${wallet()}\n`, split)), { id: ID, csrf: CSRF });
});

test("anything but exactly one demo buyer and one token is treated as signed out", () => {
  const cases = {
    "signed out": `1:{"viewer":null,"demo":true}\n`,
    "no token": `1:${viewer()}\n`,
    "two tokens": `1:${viewer()}\n2:${wallet()}\n3:${wallet(OTHER)}\n`,
    "a malformed token": `1:${viewer()}\n2:{"csrf":"xyz"}\n${wallet()}\n`,
    "an operator account": `1:${viewer("account", "seller", "dealer")}\n2:${wallet()}\n`,
    "a demo seller": `1:${viewer("demo", "seller")}\n2:${wallet()}\n`,
    "a foreign id": `1:${viewer("demo", "buyer", "dealer")}\n2:${wallet()}\n`,
    "two viewers": `1:${viewer()}\n1b:${viewer()}\n2:${wallet()}\n`,
  };
  for (const [name, flight] of Object.entries(cases)) assert.equal(demoSessionFromWalletPage(walletPage(flight)), null, name);
  // A changed page contract on the exchange (another web image) must sign the visitor out, never guess.
  const changed = {
    "viewer keys reordered": `1:{"viewer":{"role":"buyer","kind":"demo","id":"${ID}"}}\n2:${wallet()}\n`,
    "viewer renamed": `1:{"account":{"kind":"demo","role":"buyer","id":"${ID}"}}\n2:${wallet()}\n`,
    "token renamed": `1:${viewer()}\n2:["$","$L14",null,{"csrfToken":"${CSRF}"}]\n`,
    "token longer": `1:${viewer()}\n2:${wallet(CSRF + "00")}\n`,
  };
  for (const [name, flight] of Object.entries(changed)) assert.equal(demoSessionFromWalletPage(walletPage(flight)), null, name);
  const otherFormat = `<script id="__NEXT_DATA__">${JSON.stringify({ viewer: { kind: "demo", role: "buyer", id: ID }, csrf: CSRF })}</script>`;
  assert.equal(demoSessionFromWalletPage(otherFormat), null, "a different inline payload format");
  assert.equal(demoSessionFromWalletPage("<html>no payload</html>"), null);
  assert.equal(demoSessionFromWalletPage('<script>self.__next_f.push([1,"\\x"])</script>'), null, "undecodable chunk");
});

const cookieFor = (claims) => `${Buffer.from(JSON.stringify(claims)).toString("base64url")}.${"a".repeat(64)}`;
const COOKIE = cookieFor({ id: ID, role: "buyer", kind: "demo", sid: "1".repeat(32), exp: Date.now() + 60000 });

test("session cookie values and their unverified claims", () => {
  assert.ok(validSessionCookie(COOKIE));
  for (const bad of ["", "abc", `${COOKIE};x`, `${COOKIE}.extra`, COOKIE.replace(/.$/, "g"), "a".repeat(2000) + "." + "a".repeat(64)]) {
    assert.equal(validSessionCookie(bad), false);
  }
  assert.deepEqual(Object.keys(cookieClaim(COOKIE)), ["id", "role", "kind", "sid", "exp"]);
  assert.equal(cookieClaim(cookieFor({ id: ID })), null);
  assert.equal(cookieClaim(`${"!".repeat(20)}.${"a".repeat(64)}`), null);
});

test("only a well-formed session cookie change from the exchange is applied", () => {
  const set = `zwa_session=${COOKIE}; Path=/; Expires=Thu, 15 Oct 2026 07:59:37 GMT; Max-Age=7199; HttpOnly; SameSite=strict`;
  assert.deepEqual(sessionCookieChange([set]), { set: COOKIE, maxAge: 7199 });
  assert.deepEqual(sessionCookieChange(["other=1; Path=/", set]), { set: COOKIE, maxAge: 7199 });
  assert.equal(sessionCookieChange([]), null);
  assert.equal(sessionCookieChange(["other=1"]), null);
  assert.deepEqual(sessionCookieChange(["zwa_session=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT"]), { clear: true });
  assert.deepEqual(sessionCookieChange(["zwa_session=x; Max-Age=0"]), { clear: true });
  assert.equal(sessionCookieChange([set, set]), "invalid");
  assert.equal(sessionCookieChange([`zwa_session=${COOKIE}; Path=/`]), "invalid", "a session cookie needs a lifetime");
  assert.equal(sessionCookieChange([`zwa_session=bad; Max-Age=60`]), "invalid");
  assert.equal(sessionCookieChange([`zwa_session=${COOKIE}; Max-Age=999999`]), "invalid");
});
