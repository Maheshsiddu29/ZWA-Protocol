// Plain-language order states. Cryptographic detail stays in technical views.
// Each progress stage completes when the order reaches its last state.
export const stages: { label: string; states: string[] }[] = [
  { label: "Exact quote", states: ["BUYER_REQUESTED", "QUOTE_READY"] },
  { label: "You confirmed", states: ["BUYER_CONFIRMED"] },
  { label: "Eligibility proof verified", states: ["PROOF_VERIFIED"] },
  { label: "Both wallets signed", states: ["TRADE_AUTHORIZED", "BUYER_APPROVED", "SELLER_APPROVED"] },
  { label: "Included in a block", states: ["SUBMITTED", "INCLUDED"] },
  { label: "Balances reconciled", states: ["RECONCILED", "RECEIPT_AVAILABLE"] },
];

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
};
export const failureText = (reason: unknown) =>
  typeof reason === "string" && reasons[reason] ? reasons[reason] : "The order did not complete.";

export const terminal = new Set(["CANCELLED", "EXPIRED", "REJECTED", "EXECUTION_FAILED", "UNCERTAIN"]);
