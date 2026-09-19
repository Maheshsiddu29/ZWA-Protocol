"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  receiverToLimbs,
  limbsToReceiver,
  createHasher,
  computeRecipientBinding,
  computeTrade,
} = require("../../circuits/shared/eligibility-v1");

const RECEIVER_A = "781671f8a41294c866d8161f3bf5f84a8fd2c328f91a2d085a66036acd59439731c36c4f1b99b4d64be233";

test("43-byte Orchard raw address round-trips through 128/128/88-bit limbs", () => {
  const limbs = receiverToLimbs(RECEIVER_A);
  assert.equal(limbsToReceiver(limbs.limb0, limbs.limb1, limbs.limb2), RECEIVER_A);
});

test("malformed raw Orchard receiver is rejected", () => {
  assert.throws(() => receiverToLimbs("01"), /43 bytes/);
});

test("subject and receiver both affect recipient commitment", async () => {
  const hash = await createHasher();
  const a = computeRecipientBinding(hash, 11n, RECEIVER_A);
  const b = computeRecipientBinding(hash, 12n, RECEIVER_A);
  const changedReceiver = `79${RECEIVER_A.slice(2)}`;
  const c = computeRecipientBinding(hash, 11n, changedReceiver);
  assert.notEqual(a.recipientCommitment, b.recipientCommitment);
  assert.notEqual(a.recipientCommitment, c.recipientCommitment);
});

test("vendored Phase 0F computeTrade remains the active V1 implementation", async () => {
  const hash = await createHasher();
  const fields = {
    offeredAssetBase: "4889ad11564115f3655f7e434bffb23074d42aafd58cfecae32a5b5eafaf5301",
    requestedAssetBase: "a7ac13ded8b51e7a59c400097b70fe6d5d855b30ad19b1897de1fd74721a9339",
    offeredAmount: 10n,
    requestedAmount: 6n,
    recipientCommitment: 17n,
    policyRoot: 19n,
    matcherFeeAmount: 5n,
    matcherFeeRecipientCommitment: 23n,
    nonce: 7001n,
    expiry: 2000000000n,
  };
  const direct = computeTrade(hash, fields).tradeCommitment;
  const phase0f = require("../../circuits/shared/trade-commitment-v1");
  const vendored = phase0f.computeTrade(hash, fields).tradeCommitment;
  assert.equal(direct, vendored);
});
