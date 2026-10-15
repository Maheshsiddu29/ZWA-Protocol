// One logical purchase: one key, same-key retries, lookup-only recovery. Run: npm test
import assert from "node:assert/strict";
import test from "node:test";
import { BUY_ATTEMPTS, classify, clearIntent, loadIntent, newIntent, saveIntent, submit } from "../components/exchange/purchase.ts";

const OFFER = "1".repeat(32), OLD = "2".repeat(32), NEW = "3".repeat(32), OTHER = "4".repeat(32);
const intent = (known = [OLD]) => newIntent(OFFER, "zwa-gold-reference", 2, known, () => "fixed-uuid-0001");
const accepted = { status: 200, data: { id: NEW, state: "BUYER_REQUESTED" } };
const mine = (id, extra = {}) => ({ id, viewer: "buyer", instrumentId: "zwa-gold-reference", quantity: 2, ...extra });

function harness(replies, orders = null) {
  const sent = [], remembered = [];
  let lookups = 0;
  const deps = {
    post: async (body) => { sent.push(body); const next = replies.shift(); if (next instanceof Error) throw next; return next ?? null; },
    orders: async () => { lookups++; return orders; },
    remember: (value) => remembered.push(value),
    sleep: async () => {},
  };
  return { deps, sent, remembered, lookups: () => lookups };
}

test("an answered purchase is accepted with the one key", async () => {
  const h = harness([accepted]);
  const { outcome } = await submit(intent(), h.deps);
  assert.deepEqual(outcome, { kind: "accepted", orderId: NEW, recovered: false });
  assert.deepEqual(h.sent, [{ offerId: OFFER, quantity: 2, idempotencyKey: "buy-fixed-uuid-0001" }]);
  assert.equal(h.lookups(), 0);
});

test("lost answers are resent with the same key and payload, and the unknown flag is persisted first", async () => {
  const h = harness([null, new Error("network"), accepted]);
  const { outcome, intent: after } = await submit(intent(), h.deps);
  assert.deepEqual(outcome, { kind: "accepted", orderId: NEW, recovered: true });
  assert.equal(h.sent.length, 3);
  assert.ok(h.sent.every(body => body.idempotencyKey === "buy-fixed-uuid-0001" && body.quantity === 2 && body.offerId === OFFER));
  assert.equal(h.remembered.length, 2);
  assert.ok(h.remembered.every(value => value.unknown && value.key === "buy-fixed-uuid-0001"));
  assert.equal(after.unknown, true);
});

test("a 503 unknown outcome (portal internal error or no answer) is retried, never treated as refused", async () => {
  const h = harness([{ status: 503, data: { outcome: "unknown" } }, accepted]);
  const { outcome } = await submit(intent(), h.deps);
  assert.equal(outcome.kind, "accepted");
  assert.equal(h.sent.length, 2);
});

test("a definite refusal with nothing unanswered ends the intent without a lookup", async () => {
  const h = harness([{ status: 409, data: { error: "the dealer has paused new sales" } }]);
  const { outcome } = await submit(intent(), h.deps);
  assert.deepEqual(outcome, { kind: "refused", error: "the dealer has paused new sales" });
  assert.equal(h.sent.length, 1);
  assert.equal(h.lookups(), 0);
});

test("a refusal after an unanswered attempt is not proof: it is resolved by lookup", async () => {
  const h = harness([null, { status: 409, data: { error: "experimental chain is unavailable" } }], [mine(OLD), mine(NEW)]);
  const { outcome } = await submit(intent(), h.deps);
  assert.deepEqual(outcome, { kind: "accepted", orderId: NEW, recovered: true });
  assert.equal(h.sent.length, 2);
  assert.equal(h.lookups(), 1);
});

test("never more than BUY_ATTEMPTS submissions; then lookup only", async () => {
  const h = harness([null, null, null, null, null], [mine(OLD)]);
  const { outcome, intent: after } = await submit(intent(), h.deps);
  assert.equal(h.sent.length, BUY_ATTEMPTS);
  assert.equal(outcome.kind, "unknown");
  assert.deepEqual(outcome.candidates, []);
  assert.equal(after.key, "buy-fixed-uuid-0001");
});

test("classification: exactly one new matching order is accepted; anything else stays unknown", () => {
  assert.deepEqual(classify(intent(), [mine(OLD), mine(NEW)]), { kind: "accepted", orderId: NEW, recovered: true });
  assert.equal(classify(intent(), null).kind, "unknown");
  assert.equal(classify(intent(), [mine(OLD)]).kind, "unknown");
  const two = classify(intent(), [mine(OLD), mine(NEW), mine(OTHER)]);
  assert.equal(two.kind, "unknown");
  assert.deepEqual(two.candidates, [NEW, OTHER]);
  // A new order for something else alongside it: not chosen automatically.
  assert.equal(classify(intent(), [mine(NEW), mine(OTHER, { quantity: 1 })]).kind, "unknown");
  assert.equal(classify(intent(), [mine(NEW, { quantity: 1 })]).kind, "unknown");
  // Without a snapshot of earlier orders nothing can be told apart.
  const blind = classify(intent(null), [mine(NEW)]);
  assert.equal(blind.kind, "unknown");
  assert.deepEqual(blind.candidates, [NEW]);
});

function memoryStore() {
  const data = new Map();
  return { getItem: k => data.get(k) ?? null, setItem: (k, v) => data.set(k, String(v)), removeItem: k => data.delete(k), data };
}

test("storage round-trips per account and rejects tampered or foreign values", () => {
  const store = memoryStore();
  const value = intent();
  assert.equal(saveIntent(store, "judge-a", value), true);
  assert.deepEqual(loadIntent(store, "judge-a"), value);
  assert.equal(loadIntent(store, "judge-b"), null);
  store.setItem("zwa.purchase.v1.judge-a", JSON.stringify({ ...value, key: "bad key!" }));
  assert.equal(loadIntent(store, "judge-a"), null);
  store.setItem("zwa.purchase.v1.judge-a", "{not json");
  assert.equal(loadIntent(store, "judge-a"), null);
  saveIntent(store, "judge-a", value);
  clearIntent(store, "judge-a");
  assert.equal(loadIntent(store, "judge-a"), null);
});

test("unavailable or throwing storage never breaks the purchase", () => {
  const throwing = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("denied"); }, removeItem() { throw new Error("denied"); } };
  assert.equal(loadIntent(throwing, "a"), null);
  assert.equal(saveIntent(throwing, "a", intent()), false);
  assert.doesNotThrow(() => clearIntent(throwing, "a"));
  assert.equal(loadIntent(null, "a"), null);
  assert.equal(saveIntent(null, "a", intent()), false);
});
