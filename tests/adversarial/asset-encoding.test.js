"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const {
  assetIdToLimbs,
  limbsToAssetId,
  assertU64,
  MAX_U128,
} = require("../../circuits/shared/trade-commitment-v1");

const VALUES = [
  "4889ad11564115f3655f7e434bffb23074d42aafd58cfecae32a5b5eafaf5301",
  "a7ac13ded8b51e7a59c400097b70fe6d5d855b30ad19b1897de1fd74721a9339",
  "00000000000000000000000000000000ffffffffffffffffffffffffffffffff",
];

test("32-byte AssetBase encodings round-trip through two 128-bit limbs", () => {
  for (const value of VALUES) {
    const limbs = assetIdToLimbs(value);
    assert.ok(limbs.hi <= MAX_U128);
    assert.ok(limbs.lo <= MAX_U128);
    assert.equal(limbsToAssetId(limbs.hi, limbs.lo), value);
  }
});

test("malformed AssetBase encoding is rejected", () => {
  assert.throws(() => assetIdToLimbs("abcd"), /exactly 32 bytes/);
  assert.throws(() => assetIdToLimbs("zz".repeat(32)), /exactly 32 bytes/);
  assert.throws(() => limbsToAssetId(1n << 128n, 0n), /128-bit/);
});

test("out-of-range u64 trade values are rejected", () => {
  assert.equal(assertU64((1n << 64n) - 1n, "amount"), (1n << 64n) - 1n);
  assert.throws(() => assertU64(1n << 64n, "amount"), /unsigned 64-bit/);
  assert.throws(() => assertU64(-1n, "amount"), /unsigned 64-bit/);
});
