// One logical purchase in the browser. Same rules as the benchmark harness (benchmark/correlation.py):
//  * one purchase intent has exactly one idempotency key, written to storage before anything is sent;
//  * a retry resends the same key and payload, never a new key;
//  * an unknown result is resolved only by reading the buyer's orders, never by another purchase.
// The exchange returns the existing order for a repeated key, and nothing is signed until the buyer
// confirms the exact quote on the order page. Kept free of imports for tests/purchase.test.mjs.

export type Intent = {
  v: 1; key: string; offerId: string; instrumentId: string; quantity: number;
  /** Order IDs the buyer already had before this intent; null if they could not be read. */
  knownOrderIds: string[] | null;
  /** True once any attempt went unanswered: a later refusal no longer proves nothing was created. */
  unknown: boolean;
};
export type Reply = { status: number; data: any } | null; // null: no HTTP answer at all
export type OrderSummary = { id: string; viewer?: string; instrumentId: string; quantity: number };
export type Outcome =
  | { kind: "accepted"; orderId: string; recovered: boolean }
  | { kind: "refused"; error: string }
  | { kind: "unknown"; reason: string; candidates: string[] };

const orderId = /^[0-9a-f]{32}$/;
export const BUY_ATTEMPTS = 3;

export function newIntent(offerId: string, instrumentId: string, quantity: number,
                          knownOrderIds: string[] | null, random: () => string): Intent {
  return { v: 1, key: `buy-${random()}`, offerId, instrumentId, quantity, knownOrderIds, unknown: false };
}

export const payload = (intent: Intent) =>
  ({ offerId: intent.offerId, quantity: intent.quantity, idempotencyKey: intent.key });

// ---------------------------------------------------------------- storage

type Store = Pick<Storage, "getItem" | "setItem" | "removeItem">;
const storageKey = (scope: string) => `zwa.purchase.v1.${scope}`;

function valid(value: any): value is Intent {
  return !!value && value.v === 1 && typeof value.key === "string" && /^[a-zA-Z0-9_-]{8,80}$/.test(value.key)
    && orderId.test(value.offerId) && typeof value.instrumentId === "string"
    && Number.isSafeInteger(value.quantity) && value.quantity >= 1 && typeof value.unknown === "boolean"
    && (value.knownOrderIds === null || (Array.isArray(value.knownOrderIds)
        && value.knownOrderIds.every((id: unknown) => typeof id === "string" && orderId.test(id))));
}

/** The unresolved intent for this account, if any. Storage can be missing or throw (private mode). */
export function loadIntent(store: Store | null, scope: string): Intent | null {
  try {
    const value = JSON.parse(store?.getItem(storageKey(scope)) ?? "null");
    return valid(value) ? value : null;
  } catch { return null; }
}

/** False when storage is unavailable; the caller still has the intent in memory for this page. */
export function saveIntent(store: Store | null, scope: string, intent: Intent): boolean {
  try { store?.setItem(storageKey(scope), JSON.stringify(intent)); return !!store; } catch { return false; }
}

export function clearIntent(store: Store | null, scope: string): void {
  try { store?.removeItem(storageKey(scope)); } catch { /* nothing stored */ }
}

// ------------------------------------------------------------- resolution

/** A definite answer from the exchange or the web layer: the request was refused, not lost. */
const definiteRefusal = (reply: Reply) => !!reply && [400, 403, 404, 409, 429].includes(reply.status);

/**
 * Candidates for this intent among the buyer's orders: new since the intent began, same asset and
 * quantity. Exactly one means the exchange accepted the intent; none or several stay unknown.
 */
export function classify(intent: Intent, orders: OrderSummary[] | null): Outcome {
  if (orders === null) return { kind: "unknown", reason: "Your orders could not be read.", candidates: [] };
  if (intent.knownOrderIds === null) {
    return { kind: "unknown", reason: "Your earlier orders were not recorded, so a new one cannot be told apart.",
             candidates: orders.filter(o => o.instrumentId === intent.instrumentId && o.quantity === intent.quantity).map(o => o.id) };
  }
  const known = new Set(intent.knownOrderIds);
  const fresh = orders.filter(o => !known.has(o.id) && orderId.test(o.id) && (o.viewer ?? "buyer") === "buyer");
  const matching = fresh.filter(o => o.instrumentId === intent.instrumentId && o.quantity === intent.quantity);
  if (matching.length === 1 && fresh.length === 1) return { kind: "accepted", orderId: matching[0].id, recovered: true };
  if (!fresh.length) return { kind: "unknown", candidates: [],
    reason: "No order from this purchase is visible yet. It may still be processing." };
  return { kind: "unknown", candidates: fresh.map(o => o.id),
           reason: "More than one new order is visible, so the matching one cannot be chosen automatically." };
}

type Deps = {
  post: (body: object) => Promise<Reply>;
  orders: () => Promise<OrderSummary[] | null>;
  /** Persist the intent's `unknown` flag before the next attempt. */
  remember: (intent: Intent) => void;
  sleep: (ms: number) => Promise<void>;
};

/** Submit (or resume) one intent. Never changes its key; resolves unknown results by lookup only. */
export async function submit(intent: Intent, deps: Deps, attempts = BUY_ATTEMPTS): Promise<{ intent: Intent; outcome: Outcome }> {
  let current = intent;
  for (let attempt = 0; attempt < attempts; attempt++) {
    const reply = await deps.post(payload(current)).catch(() => null);
    if (reply?.status === 200 && orderId.test(String(reply.data?.id))) {
      return { intent: current, outcome: { kind: "accepted", orderId: reply.data.id, recovered: current.unknown } };
    }
    if (definiteRefusal(reply)) {
      const error = typeof reply?.data?.error === "string" ? reply.data.error : "The purchase was refused.";
      // Nothing was ever unanswered: this key never produced an order.
      if (!current.unknown) return { intent: current, outcome: { kind: "refused", error } };
      break; // an earlier attempt may have been accepted; only a lookup can tell
    }
    current = { ...current, unknown: true };
    deps.remember(current);
    if (attempt + 1 < attempts) await deps.sleep(1500 * (attempt + 1));
  }
  return { intent: current, outcome: classify(current, await deps.orders().catch(() => null)) };
}
