"use strict";

// Experimental Phase 4C.1 adapter. The Poseidon and Merkle encodings reuse
// PRE-EXISTING ZK-ORIGIN components migrated into ZWA-Protocol. No frozen
// research fixture is an authorization input.
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const protocol = path.resolve(__dirname, "../../../ZWA-Protocol");
function pinFile(file, expected) {
  const actual = crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");
  if (actual !== expected) throw new Error(`authorization source pin mismatch: ${path.basename(file)}`);
}
pinFile(path.join(__dirname, "circuits/native_zec_checkout_v2.circom"), "3cf9c5bb960bc7e7f8c5b415f362a85d8a548483b6ae317358e68d8e076c6ec0");
pinFile(path.join(__dirname, "native_zec_checkout_v2.wasm"), "412fca77a69205dfdc2260454722d8759e50d08587cb91c3cfde2badafd4a0b1");
pinFile(path.join(__dirname, "checkout_final.zkey"), "1896c80d60cc53b65cb5774e8f0a417ac687d22bc3672a6ed4060039fe0650d0");
pinFile(path.join(__dirname, "verification-key-v2.json"), "80852cdbf1d9886e316f9217fda2635afb43e17c2dce8dc7cb23cc8eb5d16708");
pinFile(path.join(protocol, "circuits/shared/eligibility-v1.js"), "3b4a07fffd5ffbcbcd0b63613283af7d4d04e1b125a62b57682e0f4671f1e9ce");
pinFile(path.join(protocol, "circuits/shared/trade-commitment-v1.js"), "38f7930290dce77e9ab9bf5fdb962ac8e5b83450339fc62f8f1e40bc781fd594");
pinFile(path.join(protocol, "node_modules/snarkjs/build/main.cjs"), "e99bbb85ee536fd64476f87492665d1a382e930e4e412278a3fd8e2f987c283f");
const schema = require(path.join(protocol, "circuits/shared/eligibility-v1.js"));
const snarkjs = require(path.join(protocol, "node_modules/snarkjs"));
const ROOT_MAGIC = Buffer.from("ZWA1ROOT");
const TRADE_DOMAIN = 22062026277931314n;
const FIELD = schema.FIELD_MODULUS;
const VK_SHA256 = "80852cdbf1d9886e316f9217fda2635afb43e17c2dce8dc7cb23cc8eb5d16708";

function fail(message) { throw new Error(message); }
function readJson(file) { return JSON.parse(fs.readFileSync(file, "utf8")); }
function writePrivate(file, value) { fs.writeFileSync(file, JSON.stringify(value, null, 2) + "\n", { flag: "wx", mode: 0o600 }); }
function rawHex(value, bytes, label) {
  if (typeof value !== "string" || !new RegExp(`^[0-9a-f]{${bytes * 2}}$`).test(value)) fail(`${label}: noncanonical hex`);
  return Buffer.from(value, "hex");
}
function limbs32(hex) {
  const bytes = rawHex(hex, 32, "32-byte identifier");
  return [BigInt(`0x${bytes.subarray(16).toString("hex")}`), BigInt(`0x${bytes.subarray(0, 16).toString("hex")}`)];
}
function commitmentOfKey(der) { return BigInt(`0x${crypto.createHash("sha256").update(der).digest("hex")}`) % FIELD; }
function u64be(n) { const b = Buffer.alloc(8); b.writeBigUInt64BE(BigInt(n)); return b; }
function canonicalRoot(kind, id, version, validFrom, expiresAt, root) {
  const idBytes = Buffer.from(id, "utf8");
  if (idBytes.length === 0 || idBytes.length > 255) fail("invalid authority id");
  const rootBytes = Buffer.from(BigInt(root).toString(16).padStart(64, "0"), "hex");
  return Buffer.concat([ROOT_MAGIC, Buffer.from([kind]), u64be(version), u64be(validFrom), u64be(expiresAt), Buffer.from([idBytes.length]), idBytes, rootBytes]);
}
function keypair() {
  const { publicKey, privateKey } = crypto.generateKeyPairSync("ed25519");
  return { publicDer: publicKey.export({ type: "spki", format: "der" }), privatePem: privateKey.export({ type: "pkcs8", format: "pem" }) };
}
function signRoot(kind, id, root, key) {
  const now = Math.floor(Date.now() / 1000);
  const envelope = { kind, id, version: 1, validFrom: now - 30, expiresAt: now + 3600, root: String(root) };
  envelope.signature = crypto.sign(null, canonicalRoot(kind, id, envelope.version, envelope.validFrom, envelope.expiresAt, root), key).toString("hex");
  return envelope;
}
function verifyRoot(envelope, kind, anchor, now) {
  if (envelope.kind !== kind || envelope.id !== anchor.id || envelope.version !== anchor.version ||
      envelope.root !== anchor.root || !Number.isSafeInteger(envelope.validFrom) || !Number.isSafeInteger(envelope.expiresAt) ||
      envelope.validFrom > now || envelope.expiresAt <= now || envelope.validFrom >= envelope.expiresAt) fail(`${kind}: forged, stale or revoked root`);
  const der = Buffer.from(anchor.publicKeyDer, "base64");
  const signature = rawHex(envelope.signature, 64, `${kind} signature`);
  const key = crypto.createPublicKey({ key: der, type: "spki", format: "der" });
  if (!crypto.verify(null, canonicalRoot(kind === "issuer" ? 1 : 2, envelope.id, envelope.version, envelope.validFrom, envelope.expiresAt, envelope.root), key, signature)) fail(`${kind}: signature invalid`);
  return commitmentOfKey(der);
}
function randomField() { return BigInt(`0x${crypto.randomBytes(32).toString("hex")}`) % FIELD; }
function randomU64() { return crypto.randomBytes(8).readBigUInt64BE(); }
function treeWithLeaf(hash, leaf) {
  const levels = schema.buildMerkleTree(hash, [BigInt(leaf), 0n, 0n, 0n, 0n, 0n, 0n, 0n]);
  const proof = schema.merkleProof(levels, 0);
  return { root: levels.at(-1)[0], siblings: proof.siblings, path: proof.pathIndices };
}
function publicTerms(hash, terms, quoteDigest) {
  if (terms.network !== "regtest" || terms.matcher_fee_zat !== 0 || terms.version !== 1) fail("unsupported native ZEC checkout terms");
  const asset = schema.assetIdToLimbs(terms.asset_base);
  const receiver = schema.receiverToLimbs(terms.buyer_receive_address);
  const [quoteDigestHi, quoteDigestLo] = limbs32(quoteDigest);
  const assetCommitment = schema.computeAssetCommitment(hash, terms.asset_base);
  const receiverCommitment = hash([schema.DOMAINS_0G.RECEIVER_V1, receiver.limb0, receiver.limb1, receiver.limb2]);
  const tradeCommitment = hash([TRADE_DOMAIN, assetCommitment, receiverCommitment, quoteDigestHi, quoteDigestLo, BigInt(terms.tx_expiry_height)]);
  return { offeredAssetHi: asset.hi, offeredAssetLo: asset.lo,
    receiverLimb0: receiver.limb0, receiverLimb1: receiver.limb1, receiverLimb2: receiver.limb2,
    quoteDigestHi, quoteDigestLo, txExpiryHeight: BigInt(terms.tx_expiry_height), tradeCommitment };
}
function asStrings(value) { return schema.stringifyBigInts(value); }

async function generate(termsFile, sessionFile, issuanceFile, outputDir) {
  fs.mkdirSync(outputDir, { recursive: false, mode: 0o700 });
  const terms = readJson(termsFile), session = readJson(sessionFile), issuance = readJson(issuanceFile);
  if (session.receipt !== null || session.outcome !== "QUOTE_READY" || !/^[0-9a-f]{64}$/.test(session.sessionId)) fail("fresh prepared session required");
  if (issuance.asset_base !== terms.asset_base) fail("issued AssetBase does not match checkout");
  const hash = await schema.createHasher();
  const common = publicTerms(hash, terms, session.sessionId);
  const issuer = keypair(), authority = keypair();
  const issuerCommitment = commitmentOfKey(issuer.publicDer);
  const credentialAuthorityCommitment = commitmentOfKey(authority.publicDer);
  const investorClass = 1n, jurisdiction = 840n;
  const policy = treeWithLeaf(hash, schema.computePolicyLeaf(hash, investorClass, jurisdiction));
  const issuanceNonce = randomU64();
  const seriesCommitment = randomField();
  const issuanceLeaf = schema.computeIssuance(hash, { offeredAssetBase: terms.asset_base,
    issuerCommitment, seriesCommitment, policyRoot: policy.root, issuanceNonce }).issuanceLeaf;
  const issuanceTree = treeWithLeaf(hash, issuanceLeaf);
  const subjectSecret = randomField(), credentialNonce = randomU64();
  const approved = schema.computeRecipientBinding(hash, subjectSecret, terms.buyer_receive_address);
  const credentialExpiryHeight = BigInt(terms.tx_expiry_height);
  const credentialLeaf = schema.computeCredential(hash, { subjectSecret, credentialAuthorityCommitment,
    investorClass, jurisdiction, credentialExpiry: credentialExpiryHeight, credentialNonce,
    approvedReceiverCommitment: approved.receiverCommitment }).credentialLeaf;
  const credentialTree = treeWithLeaf(hash, credentialLeaf);
  const input = asStrings({ ...common, authorizedIssuanceRoot: issuanceTree.root,
    activeCredentialRoot: credentialTree.root, issuerCommitment, credentialAuthorityCommitment,
    seriesCommitment, policyRoot: policy.root, issuanceNonce,
    issuanceMerkleSiblings: issuanceTree.siblings, issuanceMerklePathIndices: issuanceTree.path,
    subjectSecret, investorClass, jurisdiction, credentialExpiryHeight, credentialNonce,
    credentialMerkleSiblings: credentialTree.siblings, credentialMerklePathIndices: credentialTree.path,
    allowedInvestorClass: investorClass, allowedJurisdiction: jurisdiction,
    policyMerkleSiblings: policy.siblings, policyMerklePathIndices: policy.path });
  const issuerEnvelope = signRoot(1, "disposable-issuer", issuanceTree.root, issuer.privatePem);
  issuerEnvelope.kind = "issuer";
  const credentialEnvelope = signRoot(2, "disposable-credential-authority", credentialTree.root, authority.privatePem);
  credentialEnvelope.kind = "credential";
  const trust = { version: 2, network: "regtest", issuer: { id: issuerEnvelope.id, version: 1, root: issuerEnvelope.root, publicKeyDer: issuer.publicDer.toString("base64") },
    credential: { id: credentialEnvelope.id, version: 1, root: credentialEnvelope.root, publicKeyDer: authority.publicDer.toString("base64") },
    policyRoot: String(policy.root), quoteDigest: session.sessionId };
  const wasm = path.join(__dirname, "native_zec_checkout_v2.wasm");
  const zkey = path.join(__dirname, "checkout_final.zkey");
  const { proof, publicSignals } = await snarkjs.groth16.fullProve(input, wasm, zkey);
  writePrivate(path.join(outputDir, "trust.json"), trust);
  writePrivate(path.join(outputDir, "authorization.json"), { version: 2, issuerEnvelope, credentialEnvelope, proof, publicSignals });
  // These keys are disposable and never written to disk. Root signatures are
  // produced during this call, then the process exits.
  console.log(`PHASE4C1_PROOF_GENERATED trade=${common.tradeCommitment} quote=${session.sessionId}`);
}

async function verify(termsFile, quoteDigest, trustFile, authorizationFile) {
  const terms = readJson(termsFile), trust = readJson(trustFile), auth = readJson(authorizationFile);
  if (trust.version !== 2 || trust.network !== "regtest" || auth.version !== 2 || trust.quoteDigest !== quoteDigest) fail("authorization version, network or quote mismatch");
  const now = Math.floor(Date.now() / 1000);
  const issuerCommitment = verifyRoot(auth.issuerEnvelope, "issuer", trust.issuer, now);
  const credentialAuthorityCommitment = verifyRoot(auth.credentialEnvelope, "credential", trust.credential, now);
  const hash = await schema.createHasher();
  const t = publicTerms(hash, terms, quoteDigest);
  const expected = [auth.issuerEnvelope.root, auth.credentialEnvelope.root, t.tradeCommitment,
    t.offeredAssetHi, t.offeredAssetLo, t.receiverLimb0, t.receiverLimb1, t.receiverLimb2,
    t.quoteDigestHi, t.quoteDigestLo, t.txExpiryHeight, issuerCommitment,
    trust.policyRoot, credentialAuthorityCommitment].map(String);
  if (!Array.isArray(auth.publicSignals) || auth.publicSignals.length !== expected.length ||
      expected.some((v, i) => String(auth.publicSignals[i]) !== v)) fail("proof public inputs differ from exact checkout or active roots");
  const vkPath = path.join(__dirname, "verification-key-v2.json");
  if (crypto.createHash("sha256").update(fs.readFileSync(vkPath)).digest("hex") !== VK_SHA256) fail("verification key source pin mismatch");
  const vk = readJson(vkPath);
  if (!await snarkjs.groth16.verify(vk, auth.publicSignals, auth.proof)) fail("Groth16 proof rejected");
  return { version: 2, issuerAuthentication: "VERIFIED", issuanceProvenance: "VERIFIED",
    credentialAuthorityAuthentication: "VERIFIED", investorEligibility: "VERIFIED",
    exactTradeAuthorization: "VERIFIED", quoteDigest, tradeCommitment: String(t.tradeCommitment),
    rootedNoteLineage: "UNSUPPORTED" };
}

async function main() {
  const [command, ...args] = process.argv.slice(2);
  if (command === "generate" && args.length === 4) return generate(...args);
  if (command === "verify" && args.length === 4) { console.log(JSON.stringify(await verify(...args))); return; }
  fail("usage: native_zec_v2.js generate TERMS SESSION ISSUANCE OUTPUT_DIR | verify TERMS QUOTE_DIGEST TRUST AUTHORIZATION");
}
if (require.main === module) main().then(() => process.exit(0)).catch(err => { console.error(`PHASE4C1_AUTHORIZATION_REJECTED: ${err.message}`); process.exit(1); });
module.exports = { verify, publicTerms, canonicalRoot, verifyRoot };
