"use strict";
// Negative verification vectors against an actual Phase 4E order's proof.
// Usage: node test_phase4e_order_adversarial.js ROOT ORDER_ID
// Evidence is only read; mutated copies go to a disposable private dir.
// Root envelopes are valid for one hour after proof generation, so the
// positive vector is meaningful only for a recently authorized order.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const [root, order] = process.argv.slice(2);
if (!/^\/private\/tmp\/zwa4e-[A-Za-z0-9-]+$/.test(root || "") || !/^[0-9a-f]{32}$/.test(order || ""))
  throw Error("usage: ROOT ORDER_ID");
const legacy = { ec27f52d2e7ad69287f7d4d4357025af: "exchange/order-quotes/gold-1" };
const quoteDir = path.join(root, legacy[order] || path.join("exchange/orders", order, "quote"));
const authDir = path.join(root, "exchange/orders", order, "authorization");
const terms = JSON.parse(fs.readFileSync(path.join(quoteDir, "terms.json")));
const session = JSON.parse(fs.readFileSync(path.join(quoteDir, "session-result.json")));
const trust = JSON.parse(fs.readFileSync(path.join(authDir, "trust.json")));
const auth = JSON.parse(fs.readFileSync(path.join(authDir, "authorization.json")));
const script = path.join(__dirname, "native_zec_v2.js");
const product = path.resolve(__dirname, "../..");
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "zwa4e-proof-negative-"));
fs.chmodSync(tmp, 0o700);
function run(label, mutate, accept = false) {
  const t = structuredClone(terms), r = structuredClone(trust), a = structuredClone(auth);
  mutate(t, r, a);
  for (const [name, value] of Object.entries({ terms: t, trust: r, authorization: a }))
    fs.writeFileSync(path.join(tmp, `${name}.json`), JSON.stringify(value), { mode: 0o600 });
  const quote = spawnSync("python3", ["-c", "import json,sys; from checkout.quote import from_terms; print(from_terms(json.load(open(sys.argv[1])))['quoteDigest'])",
    path.join(tmp, "terms.json")], { encoding: "utf8", timeout: 10000,
    env: { ...process.env, PYTHONPATH: product } });
  if (quote.status !== 0) {
    if (accept) throw Error(`${label}: exact quote rejected`);
    console.log(`PHASE4E_NEGATIVE_PASS ${label} (quote rejected)`);
    return;
  }
  const result = spawnSync(process.execPath, [script, "verify", path.join(tmp, "terms.json"),
    quote.stdout.trim(), path.join(tmp, "trust.json"), path.join(tmp, "authorization.json")],
    { encoding: "utf8", timeout: 30000 });
  if ((result.status === 0) !== accept) throw Error(`${label}: unexpected verification result`);
  console.log(`PHASE4E_${accept ? "POSITIVE" : "NEGATIVE"}_PASS ${label}`);
}
try {
  run("exact_completed_order", () => {}, true);
  run("changed_payment", t => { t.price_zat += 1; });
  run("changed_asset", t => { t.asset_base = "ab".repeat(32); });
  run("changed_receiver", t => { t.buyer_receive_address = "ab".repeat(43); });
  run("changed_seller_proceeds", t => { t.seller_proceeds_address = "cd".repeat(43); });
  run("changed_expiry", t => { t.tx_expiry_height += 1; });
  run("forged_issuer", (_t, _r, a) => { a.issuerEnvelope.signature = "00".repeat(64); });
  run("revoked_credential_root", (_t, r) => { r.credential.root = "1"; });
  run("stale_authority", (_t, _r, a) => { a.credentialEnvelope.expiresAt = 1; });
  run("invalid_groth16", (_t, _r, a) => { a.proof.pi_a[0] = "1"; });
  run("changed_public_trade", (_t, _r, a) => { a.publicSignals[2] = "1"; });
  run("replayed_quote_digest", (_t, r) => { r.quoteDigest = "11".repeat(32); });
} finally {
  fs.rmSync(tmp, { recursive: true, force: true });
}
