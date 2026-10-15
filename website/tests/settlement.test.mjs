// When the website may say "Settlement verified", and how progress is shown. Run: npm test
import assert from "node:assert/strict";
import test from "node:test";
import { forward, progress, verdict } from "../lib/order-labels.ts";

const through = (state) => forward.slice(0, forward.indexOf(state) + 1).map(to_state => ({ to_state }));
const receipt = (durability) => ({ txid: "ab".repeat(32), durability });
const order = (state, extra = {}) => ({ state, events: through(state), chainActive: true, settlementDropped: false,
  effects: null, receipt: null, ...extra });
const settled = (extra = {}) => order("RECEIPT_AVAILABLE", { effects: { assetUnits: 1, nativeZat: -1000 },
  receipt: receipt("NODE_RESTART_DURABLE"), ...extra });

test("verified only with a live receipt, reconciliation, durability and no dropped block", () => {
  assert.equal(verdict(settled()), "VERIFIED");
  assert.equal(verdict(settled({ receipt: receipt(null) })), "CONFIRMING");
  assert.equal(verdict(settled({ receipt: null })), "NOT_VISIBLE");
  assert.equal(verdict(settled({ effects: null })), "NOT_VISIBLE");
  assert.equal(verdict(settled({ chainActive: false })), "NOT_VISIBLE");
  assert.equal(verdict(settled({ settlementDropped: true })), "NOT_VISIBLE");
});

test("no state before RECEIPT_AVAILABLE is ever verified, even with receipt-like fields", () => {
  for (const state of forward.slice(0, -1)) {
    assert.equal(verdict(order(state, { receipt: receipt("x"), effects: {} })), "IN_PROGRESS", state);
  }
});

test("failures and unresolved orders are never verified", () => {
  assert.equal(verdict(order("UNCERTAIN", { receipt: receipt("x"), effects: {} })), "UNCERTAIN");
  for (const state of ["CANCELLED", "EXPIRED", "REJECTED", "EXECUTION_FAILED"]) {
    assert.equal(verdict(order(state)), "NOT_SETTLED", state);
  }
});

test("progress follows the ledger and marks the current stage", () => {
  const stages = progress(order("BUYER_APPROVED"));
  const status = Object.fromEntries(stages.map(s => [s.label, s.status]));
  assert.equal(status["Buyer wallet signed"], "done");
  assert.equal(status["Dealer wallet signed"], "current");
  assert.equal(status["Transaction submitted"], "pending");
  assert.equal(stages.filter(s => s.status === "current").length, 1);
});

test("receipt and durability stages need the receipt, not just the state", () => {
  const status = (o) => Object.fromEntries(progress(o).map(s => [s.label, s.status]));
  assert.equal(status(settled({ receipt: null }))["Balances reconciled"], "done");
  assert.equal(status(settled({ receipt: null }))["Receipt verified on chain"], "current");
  assert.equal(status(settled({ receipt: receipt(null) }))["Receipt verified on chain"], "done");
  assert.equal(status(settled({ receipt: receipt(null) }))["Durable"], "current");
  assert.ok(progress(settled()).every(s => s.status === "done"));
});

test("a failed order marks the first unreached stage failed and nothing after it done", () => {
  const rejected = { ...order("PROOF_VERIFIED"), state: "REJECTED",
    events: [...through("PROOF_VERIFIED"), { to_state: "REJECTED" }] };
  const stages = progress(rejected);
  const index = stages.findIndex(s => s.status === "failed");
  assert.equal(stages[index].label, "Signing and proving");
  assert.ok(stages.slice(0, index).every(s => s.status === "done"));
  assert.ok(stages.slice(index + 1).every(s => s.status === "pending"));
});

test("an uncertain order after inclusion keeps inclusion done but never shows a receipt stage done", () => {
  const held = { ...order("INCLUDED"), state: "UNCERTAIN", events: [...through("INCLUDED"), { to_state: "UNCERTAIN" }] };
  const status = Object.fromEntries(progress(held).map(s => [s.label, s.status]));
  assert.equal(status["Included in a block"], "done");
  assert.equal(status["Balances reconciled"], "failed");
  assert.equal(status["Receipt verified on chain"], "pending");
});
