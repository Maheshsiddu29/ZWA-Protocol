"use strict";

const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const test = require("node:test");
const {
  createHasher,
  computeIssuance,
  computeTrade,
} = require("../../circuits/shared/trade-commitment-v1");

const fixturePath = path.resolve(__dirname, "../fixtures/reference-trade-v1.json");

test("reference issuance and trade commitments recompute deterministically", async () => {
  const fixture = JSON.parse(fs.readFileSync(fixturePath, "utf8"));
  const hash = await createHasher();
  const fields = {
    offeredAssetBase: fixture.trade.offeredAssetBase,
    requestedAssetBase: fixture.trade.requestedAssetBase,
    issuerCommitment: fixture.issuance.issuerCommitment,
    seriesCommitment: fixture.issuance.seriesCommitment,
    policyRoot: fixture.trade.policyRoot,
    issuanceNonce: fixture.issuance.issuanceNonce,
    offeredAmount: fixture.trade.offeredAmount,
    requestedAmount: fixture.trade.requestedAmount,
    recipientCommitment: fixture.trade.recipientCommitment,
    matcherFeeAmount: fixture.trade.matcherFeeAmountZatoshis,
    matcherFeeRecipientCommitment: fixture.trade.matcherFeeRecipientCommitment,
    nonce: fixture.trade.nonce,
    expiry: fixture.trade.expiry,
  };
  const issuance = computeIssuance(hash, fields);
  const trade = computeTrade(hash, fields);
  assert.equal(issuance.issuanceLeaf.toString(), fixture.issuance.issuanceLeaf);
  assert.equal(trade.tradeCommitment.toString(), fixture.trade.tradeCommitment);
});
