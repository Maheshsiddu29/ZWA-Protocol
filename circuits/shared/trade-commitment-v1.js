"use strict";

const { buildPoseidon } = require("circomlibjs");

const FIELD_MODULUS = BigInt("21888242871839275222246405745257275088548364400416034343698204186575808495617");
const MAX_U64 = (1n << 64n) - 1n;
const MAX_U128 = (1n << 128n) - 1n;

const DOMAINS = Object.freeze({
  ASSET_V1: 18387490596738609n,
  ISSUANCE_META_V1: 5283658379176460593n,
  ISSUANCE_V1: 20639290677876273n,
  FEE_V1: 301809882673n,
  TRADE_A_V1: 23734351151715889n,
  TRADE_B_V1: 23734351168493105n,
  TRADE_META_V1: 23734351353042481n,
  TRADE_V1: 6075990608753677873n,
  FEE_RECIPIENT_V1: 19793697433736753n,
  RECIPIENT_V1: 90449063990833n,
  ZEC_ASSET_TAG: 5915971n,
});

function assertHexBytes(hex, length, label) {
  if (typeof hex !== "string" || !new RegExp(`^[0-9a-fA-F]{${length * 2}}$`).test(hex)) {
    throw new Error(`${label} must be exactly ${length} bytes of hexadecimal`);
  }
}

function littleEndianBytesToBigInt(bytes) {
  const reversed = Buffer.from(bytes).reverse();
  const hex = reversed.toString("hex");
  return hex.length === 0 ? 0n : BigInt(`0x${hex}`);
}

function bigIntToLittleEndianBytes(value, length) {
  if (value < 0n || value >= (1n << BigInt(length * 8))) {
    throw new Error(`integer does not fit in ${length} bytes`);
  }
  const be = value.toString(16).padStart(length * 2, "0");
  return Buffer.from(be, "hex").reverse();
}

function assetIdToLimbs(assetBaseHex) {
  assertHexBytes(assetBaseHex, 32, "canonical AssetBase encoding");
  const bytes = Buffer.from(assetBaseHex, "hex");
  const lo = littleEndianBytesToBigInt(bytes.subarray(0, 16));
  const hi = littleEndianBytesToBigInt(bytes.subarray(16, 32));
  if (lo > MAX_U128 || hi > MAX_U128) throw new Error("internal 128-bit limb overflow");
  return { hi, lo };
}

function limbsToAssetId(hi, lo) {
  const hiValue = BigInt(hi);
  const loValue = BigInt(lo);
  if (hiValue < 0n || hiValue > MAX_U128 || loValue < 0n || loValue > MAX_U128) {
    throw new Error("AssetBase limbs must be unsigned 128-bit integers");
  }
  return Buffer.concat([
    bigIntToLittleEndianBytes(loValue, 16),
    bigIntToLittleEndianBytes(hiValue, 16),
  ]).toString("hex");
}

function assertU64(value, label) {
  const n = BigInt(value);
  if (n < 0n || n > MAX_U64) throw new Error(`${label} must be an unsigned 64-bit integer`);
  return n;
}

function assertField(value, label) {
  const n = BigInt(value);
  if (n < 0n || n >= FIELD_MODULUS) throw new Error(`${label} must be a canonical BN254 scalar`);
  return n;
}

async function createHasher() {
  const poseidon = await buildPoseidon();
  const F = poseidon.F;
  return (values) => BigInt(F.toString(poseidon(values.map(BigInt))));
}

function splitBytesIntoLittleEndianLimbs(hex) {
  if (typeof hex !== "string" || hex.length % 2 !== 0 || !/^[0-9a-fA-F]*$/.test(hex)) {
    throw new Error("canonical byte encoding must be hexadecimal");
  }
  const bytes = Buffer.from(hex, "hex");
  const limbs = [];
  for (let offset = 0; offset < bytes.length; offset += 16) {
    limbs.push(littleEndianBytesToBigInt(bytes.subarray(offset, Math.min(offset + 16, bytes.length))));
  }
  return { byteLength: bytes.length, limbs };
}

function commitCanonicalBytes(hash, domain, hex) {
  const { byteLength, limbs } = splitBytesIntoLittleEndianLimbs(hex);
  let state = hash([domain, BigInt(byteLength), BigInt(limbs.length)]);
  for (let i = 0; i < limbs.length; i += 1) {
    state = hash([state, limbs[i], BigInt(i)]);
  }
  return state;
}

function computeAssetCommitment(hash, assetBaseHex) {
  const { hi, lo } = assetIdToLimbs(assetBaseHex);
  return hash([DOMAINS.ASSET_V1, hi, lo]);
}

function computeIssuance(hash, fields) {
  const assetCommitment = computeAssetCommitment(hash, fields.offeredAssetBase);
  const issuanceMeta = hash([
    DOMAINS.ISSUANCE_META_V1,
    assertField(fields.seriesCommitment, "series commitment"),
    assertField(fields.policyRoot, "policy root"),
    assertU64(fields.issuanceNonce, "issuance nonce"),
  ]);
  const issuanceLeaf = hash([
    DOMAINS.ISSUANCE_V1,
    assertField(fields.issuerCommitment, "issuer commitment"),
    assetCommitment,
    issuanceMeta,
  ]);
  return { assetCommitment, issuanceMeta, issuanceLeaf };
}

function computeTrade(hash, fields) {
  const offeredAssetCommitment = computeAssetCommitment(hash, fields.offeredAssetBase);
  const requestedAssetCommitment = computeAssetCommitment(hash, fields.requestedAssetBase);
  const feeCommitment = hash([
    DOMAINS.FEE_V1,
    DOMAINS.ZEC_ASSET_TAG,
    assertU64(fields.matcherFeeAmount, "matcher fee amount"),
    assertField(fields.matcherFeeRecipientCommitment, "matcher fee recipient commitment"),
  ]);
  const tradePartA = hash([
    DOMAINS.TRADE_A_V1,
    offeredAssetCommitment,
    assertU64(fields.offeredAmount, "offered amount"),
    requestedAssetCommitment,
  ]);
  const tradePartB = hash([
    DOMAINS.TRADE_B_V1,
    assertU64(fields.requestedAmount, "requested amount"),
    assertField(fields.recipientCommitment, "recipient commitment"),
    assertField(fields.policyRoot, "policy root"),
  ]);
  const tradeMeta = hash([
    DOMAINS.TRADE_META_V1,
    feeCommitment,
    assertU64(fields.nonce, "trade nonce"),
    assertU64(fields.expiry, "trade expiry"),
  ]);
  const tradeCommitment = hash([
    DOMAINS.TRADE_V1,
    tradePartA,
    tradePartB,
    tradeMeta,
  ]);
  return {
    offeredAssetCommitment,
    requestedAssetCommitment,
    feeCommitment,
    tradePartA,
    tradePartB,
    tradeMeta,
    tradeCommitment,
  };
}

function buildMerkleTree(hash, leaves) {
  if (leaves.length === 0 || (leaves.length & (leaves.length - 1)) !== 0) {
    throw new Error("Merkle leaf count must be a nonzero power of two");
  }
  const levels = [leaves.map(BigInt)];
  while (levels[levels.length - 1].length > 1) {
    const prior = levels[levels.length - 1];
    const next = [];
    for (let i = 0; i < prior.length; i += 2) next.push(hash([prior[i], prior[i + 1]]));
    levels.push(next);
  }
  return levels;
}

function merkleProof(levels, index) {
  if (!Number.isInteger(index) || index < 0 || index >= levels[0].length) throw new Error("invalid leaf index");
  const siblings = [];
  const pathIndices = [];
  let cursor = index;
  for (let level = 0; level < levels.length - 1; level += 1) {
    siblings.push(levels[level][cursor ^ 1]);
    pathIndices.push(cursor & 1);
    cursor >>= 1;
  }
  return { siblings, pathIndices };
}

function stringifyBigInts(value) {
  if (typeof value === "bigint") return value.toString();
  if (Array.isArray(value)) return value.map(stringifyBigInts);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, stringifyBigInts(item)]));
  }
  return value;
}

module.exports = {
  DOMAINS,
  FIELD_MODULUS,
  MAX_U64,
  MAX_U128,
  assetIdToLimbs,
  limbsToAssetId,
  assertU64,
  assertField,
  createHasher,
  commitCanonicalBytes,
  computeAssetCommitment,
  computeIssuance,
  computeTrade,
  buildMerkleTree,
  merkleProof,
  stringifyBigInts,
};
