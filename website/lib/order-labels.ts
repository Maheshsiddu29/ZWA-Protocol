// Plain-language order states. Cryptographic detail stays in technical views.
// Kept free of imports: the same rules are unit-tested in tests/settlement.test.mjs.

/** The ledger's forward path (exchange/order_ledger.py), in order. */
export const forward = ["BUYER_REQUESTED", "QUOTE_READY", "BUYER_CONFIRMED", "PROOF_VERIFIED", "TRADE_AUTHORIZED",
  "BUYER_APPROVED", "SELLER_APPROVED", "SUBMITTED", "INCLUDED", "RECONCILED", "RECEIPT_AVAILABLE"];

/** What the progress view needs from a portal order view. */
export type OrderFacts = {
  state: string; events: { to_state: string }[]; chainActive: boolean; settlementDropped?: boolean;
  effects: unknown | null; receipt: { durability: string | null } | null;
};
export type StageStatus = "done" | "current" | "failed" | "pending";

// Each stage is done once the order has reached `state` on its forward path; the last two need more
// than a state: a receipt the portal has just re-checked against the live block, then the durability mark.
const stageList: { label: string; detail: string; state: string; extra?: (o: OrderFacts) => boolean }[] = [
  { label: "Order accepted", detail: "The exchange recorded your request and reserved the units.", state: "BUYER_REQUESTED" },
  { label: "Exact quote ready", detail: "Price, quantity and chain heights fixed in one digest.", state: "QUOTE_READY" },
  { label: "You authorized the terms", detail: "Your confirmation of that exact digest.", state: "BUYER_CONFIRMED" },
  { label: "Eligibility proof verified", detail: "Groth16 issuer and eligibility proof checked before signing.", state: "PROOF_VERIFIED" },
  { label: "Signing and proving", detail: "Restricted signers build the Action Groups.", state: "TRADE_AUTHORIZED" },
  { label: "Buyer wallet signed", detail: "Its Action Group, for these terms only.", state: "BUYER_APPROVED" },
  { label: "Dealer wallet signed", detail: "Its Action Group, under the standing policy.", state: "SELLER_APPROVED" },
  { label: "Transaction submitted", detail: "The atomic swap was sent to the regtest node.", state: "SUBMITTED" },
  { label: "Included in a block", detail: "The node returned the transaction in a block.", state: "INCLUDED" },
  { label: "Balances reconciled", detail: "Both wallets rescanned; changes equal the quote exactly.", state: "RECONCILED" },
  { label: "Receipt verified on chain", detail: "Receipt matches the block the node serves now.", state: "RECEIPT_AVAILABLE",
    extra: o => !!o.receipt && o.chainActive && !o.settlementDropped },
  { label: "Durable", detail: "Extra blocks mined; survives a node restart (local policy, not finality).", state: "RECEIPT_AVAILABLE",
    extra: o => !!o.receipt?.durability && o.chainActive && !o.settlementDropped },
];

/** Furthest forward state the order has ever reached (by its event history and current state). */
function reachedRank(order: OrderFacts): number {
  return Math.max(forward.indexOf(order.state), ...order.events.map(e => forward.indexOf(e.to_state)));
}

export function progress(order: OrderFacts): { label: string; detail: string; status: StageStatus }[] {
  const rank = reachedRank(order);
  const failed = terminal.has(order.state);
  let open = false;
  return stageList.map(stage => {
    const done = !open && rank >= forward.indexOf(stage.state) && (!stage.extra || stage.extra(order));
    if (done) return { label: stage.label, detail: stage.detail, status: "done" as const };
    const status = open ? "pending" as const : failed ? "failed" as const : "current" as const;
    open = true;
    return { label: stage.label, detail: stage.detail, status };
  });
}

export type Verdict = "VERIFIED" | "CONFIRMING" | "NOT_VISIBLE" | "IN_PROGRESS" | "UNCERTAIN" | "NOT_SETTLED";

/**
 * The only path to VERIFIED ("Settlement verified"): the ledger reached RECEIPT_AVAILABLE (after inclusion
 * and reconciliation), the reconciled wallet effects are present, the portal has just re-checked the saved
 * receipt against the live block, the durability mark exists, and the settlement was not dropped.
 */
export function verdict(order: OrderFacts): Verdict {
  if (order.state === "UNCERTAIN") return "UNCERTAIN";
  if (terminal.has(order.state)) return "NOT_SETTLED";
  if (order.state !== "RECEIPT_AVAILABLE") return "IN_PROGRESS";
  if (!order.receipt || !order.chainActive || order.settlementDropped || order.effects == null) return "NOT_VISIBLE";
  return order.receipt.durability ? "VERIFIED" : "CONFIRMING";
}

export const stateLabel: Record<string, string> = {
  BUYER_REQUESTED: "Preparing your quote", QUOTE_READY: "Review your quote",
  BUYER_CONFIRMED: "Verifying eligibility", PROOF_VERIFIED: "Eligibility verified",
  TRADE_AUTHORIZED: "Collecting wallet signatures", BUYER_APPROVED: "Waiting for the seller's signature",
  SELLER_APPROVED: "Submitting", SUBMITTED: "Submitted", INCLUDED: "Reconciling balances",
  RECONCILED: "Writing receipt", RECEIPT_AVAILABLE: "Settled", CANCELLED: "Cancelled",
  EXPIRED: "Expired", REJECTED: "Not executed", EXECUTION_FAILED: "Not settled",
  UNCERTAIN: "Held for review",
};

export const approvalLabel: Record<string, string> = {
  CUSTODIAL_DEMO_POLICY: "Demo wallet policy signer (server-held)",
  HUMAN_APPROVED: "Approved by the owner in the wallet",
  SCRIPTED_TEST_MODE: "Scripted test signer",
};

const reasons: Record<string, string> = {
  QUOTE_NOT_CONFIRMED_BEFORE_EXPIRY: "The quote expired before it was confirmed.",
  CHAIN_ADVANCED_BEFORE_AUTHORIZATION: "The chain moved on before the trade could be authorized. Start a new order.",
  CHAIN_ADVANCED_BEFORE_SIGNING: "The chain moved on before signing. Start a new order.",
  WALLET_COMPANIONS_NOT_CONNECTED: "A wallet signer was not available in time.",
  SELLER_SPENDABLE_INVENTORY_INSUFFICIENT: "The seller no longer has enough inventory.",
  BUYER_NATIVE_ZEC_INSUFFICIENT: "Your wallet does not hold enough ZEC.",
  GROTH16_PROOF_GENERATION_FAILED: "The eligibility proof could not be generated.",
  GROTH16_AUTHORIZATION_REJECTED: "The eligibility proof was rejected.",
  WALLET_REVIEW_REJECTED_TERMS: "A wallet rejected the trade terms during review.",
  BUYER_WALLET_REJECTED: "Your wallet's signer declined the trade.",
  SELLER_WALLET_REJECTED: "The seller's signer declined the trade.",
  COORDINATOR_AUTHORIZATION_REJECTED: "The settlement coordinator rejected the authorization.",
  WALLET_APPROVAL_TIMEOUT_OR_COORDINATOR_FAILURE: "Signing did not complete in time.",
  EXPIRED_WITHOUT_EXECUTION: "The transaction was not accepted and has expired. Nothing moved.",
  DEMO_SESSION_EXPIRED: "Your demo session ended before the order was signed.",
  COORDINATOR_STOPPED_AFTER_BOTH_APPROVALS: "Both wallets signed, but the settlement coordinator stopped before reporting whether the transaction was accepted.",
  WALLET_SCANS_DO_NOT_MATCH_EXACT_EFFECTS: "A transaction was included, but the wallet rescans did not match the exact quote, so it is not reported as settled.",
  ACCEPTED_WITHOUT_ATTESTED_APPROVAL: "The coordinator reported acceptance without a recorded signer attestation.",
};
export const failureText = (reason: unknown) =>
  typeof reason === "string" && reasons[reason] ? reasons[reason] : "The order did not complete.";

export const terminal = new Set(["CANCELLED", "EXPIRED", "REJECTED", "EXECUTION_FAILED", "UNCERTAIN"]);
