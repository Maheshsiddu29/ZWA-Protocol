// Plain-language order states. Cryptographic detail stays in technical views.
export const steps: [string, string][] = [
  ["BUYER_REQUESTED", "Purchase started"], ["QUOTE_READY", "Exact quote ready"],
  ["BUYER_CONFIRMED", "Purchase requested"], ["PROOF_VERIFIED", "Eligibility proof verified"],
  ["TRADE_AUTHORIZED", "Trade authorized"], ["BUYER_APPROVED", "Buyer wallet approved"],
  ["SELLER_APPROVED", "Seller wallet approved"], ["SUBMITTED", "Transaction submitted"],
  ["INCLUDED", "Included in a block"], ["RECONCILED", "Wallet balances confirmed"],
  ["RECEIPT_AVAILABLE", "Receipt available"],
];

export const stateLabel: Record<string, string> = {
  BUYER_REQUESTED: "Preparing your exact quote", QUOTE_READY: "Quote ready for your review",
  BUYER_CONFIRMED: "Verifying eligibility", PROOF_VERIFIED: "Eligibility verified",
  TRADE_AUTHORIZED: "Waiting for wallet approvals", BUYER_APPROVED: "Waiting for the seller's wallet",
  SELLER_APPROVED: "Settling", SUBMITTED: "Settling", INCLUDED: "Confirming wallet balances",
  RECONCILED: "Writing receipt", RECEIPT_AVAILABLE: "Complete", CANCELLED: "Cancelled",
  EXPIRED: "Expired", REJECTED: "Not executable", EXECUTION_FAILED: "Not settled",
  UNCERTAIN: "Needs operator review",
};

export const terminal = new Set(["CANCELLED", "EXPIRED", "REJECTED", "EXECUTION_FAILED", "UNCERTAIN"]);
export const zat = (value: number) => `${value.toLocaleString("en-US")} zatoshis`;
