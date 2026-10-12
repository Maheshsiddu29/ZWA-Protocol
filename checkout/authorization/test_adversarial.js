"use strict";
// Run against a disposable Phase 4C.1 session directory, never a research fixture.
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const root = process.argv[2];
if (!root || !/^\/private\/tmp\/zwa4c1-[A-Za-z0-9-]+$/.test(root) || !process.argv[3])
  throw Error("provide disposable Phase 4C.1 ROOT and unrelated disposable terms for negative cases");
const unrelated = JSON.parse(fs.readFileSync(process.argv[3]));
const script = path.join(__dirname, "native_zec_v2.js");
const bin = process.env.TX_TOOL_BIN || "/private/tmp/zwa4c1-tx-tool/target/release/zcash_tx_tool";
const terms = JSON.parse(fs.readFileSync(path.join(root, "exchange/terms.json")));
const trust = JSON.parse(fs.readFileSync(path.join(root, "coordinator/authorization/trust.json")));
const auth = JSON.parse(fs.readFileSync(path.join(root, "coordinator/authorization/authorization.json")));
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "zwa4c1-negative-"));
function run(label, change, accepted = false) {
  const t = structuredClone(terms), a = structuredClone(auth), r = structuredClone(trust);
  change(t, a, r);
  for (const [name, value] of Object.entries({ terms: t, authorization: a, trust: r }))
    fs.writeFileSync(path.join(tmp, `${name}.json`), JSON.stringify(value));
  const quoteResult = spawnSync(bin, ["purchase-coordinator", "--home", path.join(root, "coordinator"),
    "quote", "--terms", path.join(tmp, "terms.json")], { encoding: "utf8", timeout: 20000 });
  if (quoteResult.status !== 0) {
    if (accepted) throw Error(`${label}: valid terms rejected: ${quoteResult.stderr}`);
    console.log(`PHASE4C1_NEGATIVE_PASS ${label} (coordinator terms rejected)`);
    return;
  }
  const actualQuote = JSON.parse(quoteResult.stdout.slice(quoteResult.stdout.indexOf("{"))).quoteDigest;
  const result = spawnSync(process.execPath, [script, "verify", path.join(tmp, "terms.json"),
    actualQuote, path.join(tmp, "trust.json"), path.join(tmp, "authorization.json")], { encoding: "utf8", timeout: 20000 });
  if ((result.status === 0) !== accepted) throw Error(`${label}: unexpected verifier result: ${result.stderr || result.stdout}`);
  console.log(`PHASE4C1_${accepted ? "POSITIVE" : "NEGATIVE"}_PASS ${label}`);
}
try {
  run("fresh_authorization", () => {}, true);
  run("forged_issuer_root", (_t, a) => { a.issuerEnvelope.signature = "00".repeat(64); });
  run("stale_issuer_root", (_t, a) => { a.issuerEnvelope.expiresAt = 1; });
  run("unauthorized_zsa", t => { t.asset_base = unrelated.asset_base; });
  run("invalid_groth16_proof", (_t, a) => { a.proof.pi_a[0] = "1"; });
  run("revoked_credential_root", (_t, _a, r) => { r.credential.root = "1"; });
  run("wrong_approved_receiver", t => { t.buyer_receive_address = unrelated.buyer_receive_address; });
  run("inconsistent_public_commitment", (_t, a) => { a.publicSignals[2] = "1"; });
  run("changed_quote_amount", t => { t.price_zat += 1; });
  run("changed_seller_payout", t => { t.seller_proceeds_address = unrelated.seller_proceeds_address; });
  run("changed_sponsor_network_fee", t => { t.network_fee_zat += 1; });
  run("changed_matcher_fee", t => { t.matcher_fee_zat = 1; });
  run("wrong_network", t => { t.network = "mainnet"; });
  run("expired_authority_root", (_t, a) => { a.credentialEnvelope.expiresAt = 1; });
  run("replayed_quote_nonce", t => { t.nonce = "11".repeat(32); });
  run("unrelated_historical_lineage", (_t, a) => { a.proof = { pi_a: ["1", "1", "1"] }; });
} finally { fs.rmSync(tmp, { recursive: true, force: true }); }
