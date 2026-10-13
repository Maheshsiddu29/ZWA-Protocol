import "server-only";

import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import path from "node:path";
import type { NextRequest } from "next/server";
import { csrfValid, currentSession, demoMode, sessionHash, type Session } from "@/lib/exchange-auth";
import { allow } from "@/lib/rate-limit";
import { requestOrigin } from "@/lib/request-origin";

const accountId = /^[a-zA-Z0-9_-]{1,80}$/;
const rootPattern = /^(?:\/private\/tmp|\/var\/lib\/zwa)\/zwa4e-[A-Za-z0-9-]+$/;

export type PrivateQuote = {
  digest: string; assetBase: string; quantity: number; paymentZat: number;
  priceZatPerUnit: number | null; priceClassification: string; networkFeeZat: number;
  feePayer: string; matcherFeeZat: number; network: string; targetHeight: number;
  expiryHeight: number; groupExpiryHeight: number; buyerReceiverCommitment: string;
  sellerProceedsCommitment: string; validUntil: number | null;
};
export type PrivateReceipt = {
  orderId: string; txid: string; height: number; blockHash: string; paymentZat: number;
  quantity: number; approvalMode: string; quoteDigest: string; instrumentId: string;
  assetBase: string; priceZatPerUnit?: number; priceClassification?: string;
  confirmations: number; durability: string | null;
};
export type PrivateOrder = {
  id: string; state: string; viewer: "buyer" | "seller"; buyerId: string; sellerId: string;
  instrumentId: string; assetBase: string; quantity: number; quote: PrivateQuote | null;
  events: { sequence: number; from_state: string | null; to_state: string; occurred_at: number }[];
  note: string | null; proofVerified: boolean;
  proof: Record<string, string | null> | null;
  buyerApprovalMode: string | null; sellerApprovalMode: string | null; executionMode: string | null;
  awaitingWallet: "buyer" | "seller" | null; ownCompanionOnline: boolean | null;
  failure: Record<string, unknown> | null;
  effects: { assetUnits: number; nativeZat: number | null } | null;
  chainActive: boolean; chainHeight: number | null; receipt: PrivateReceipt | null;
  settlementDropped?: boolean;
};
export type MarketOffer = {
  offerId: string; instrumentId: string; name: string; issuerId: string; assetBase: string;
  issuanceTxid: string; issuanceHeight: number; availableUnits: number; inventoryVerifiedAtTip: boolean;
  inventoryScanHeight?: number | null;
  priceZatPerUnit: number | null; priceClassification: string | null; priceVersion: number | null;
  sellerId: string | null;
};
export type Market = {
  chainActive: boolean; chainHeight?: number; instruments: MarketOffer[];
  demo?: { dealerPolicy: "ACTIVE" | "PAUSED"; maxUnitsPerOrder: number; maxPaymentZatPerOrder: number };
};
export type Holding = { instrumentId: string; assetBase: string; units: number; scanHeight: number };
/** Pairing status (owner mode), the judge's custodial wallet, or the dealer desk. */
export type WalletStatus = {
  paired: boolean; role: "buyer" | "seller"; approvalMode: string; method?: string;
  walletAddress?: string | null; pairedAt?: number; expiresAt?: number; companionOnline?: boolean;
  mode?: "CUSTODIAL_DEMO_WALLET" | "DEMO_DEALER_STANDING_POLICY";
  state?: "REQUESTED" | "CREATED" | "FUNDING" | "READY" | "EXPIRED" | "RETIRED" | "FAILED";
  fundingTxid?: string | null; fundingHeight?: number | null; createdAt?: number; readyAt?: number | null;
  nativeZat?: number | null; scanHeight?: number | null; holdings?: Holding[];
  ordersUsed?: number; openOrderId?: string | null; chainActive?: boolean; chainHeight?: number | null;
  limits?: { maxOrders?: number; maxUnitsPerOrder: number; maxPaymentZatPerOrder: number; maxSponsoredOrdersPerHour?: number };
  signerOnline?: boolean; policy?: "ACTIVE" | "PAUSED"; sponsoredLastHour?: number;
};
export type BridgeResult<T> = { ok: true; data: T } | { ok: false; error: string };

async function participant(): Promise<Session | null> {
  const session = await currentSession();
  if (!session || !accountId.test(session.id)) return null;
  if (session.kind === "demo") return demoMode() ? session : null;
  // Operator-provisioned accounts are pinned to the configured participants.
  const expected = session.role === "buyer" ? process.env.ZWA_PORTFOLIO_BUYER_ID : process.env.ZWA_PORTFOLIO_SELLER_ID;
  if (!expected || session.id !== expected) return null;
  return session;
}

/** Container deployment: the engine's private portal API (no shared volumes). */
async function remotePortal(url: string, request: object): Promise<BridgeResult<unknown> | null> {
  const token = process.env.ZWA_PORTAL_TOKEN ?? "";
  if (token.length < 32 || !/^http:\/\/[a-z][a-z0-9-]{0,62}:[0-9]{2,5}$/.test(url)) return null;
  try {
    const response = await fetch(`${url}/v1/portal`, { method: "POST", cache: "no-store", signal: AbortSignal.timeout(20000),
      headers: { "content-type": "application/json", authorization: `Bearer ${token}` }, body: JSON.stringify(request) });
    const text = await response.text();
    if (!response.ok || text.length > 262144) return null;
    return JSON.parse(text) as BridgeResult<unknown>;
  } catch { return null; }
}

function runPortal(request: object): Promise<BridgeResult<unknown> | null> {
  const remote = process.env.ZWA_PORTAL_URL;
  if (remote) return remotePortal(remote, request);
  const project = path.resolve(process.cwd(), "..");
  return new Promise((resolve) => {
    const child = spawn(process.env.ZWA_PYTHON || "python3", ["-m", "exchange.portal"], {
      cwd: project, env: { NODE_ENV: process.env.NODE_ENV, PATH: process.env.PATH ?? "/usr/bin:/bin", PYTHONPATH: project,
        ZWA_PRIVATE_EXCHANGE_ROOT: process.env.ZWA_PRIVATE_EXCHANGE_ROOT ?? "" },
      stdio: ["pipe", "pipe", "ignore"],
    });
    let output = "";
    const timer = setTimeout(() => child.kill("SIGKILL"), 20000);
    child.stdout.on("data", (chunk: Buffer) => {
      output += chunk.toString();
      if (output.length > 262144) child.kill("SIGKILL");
    });
    child.on("error", () => { clearTimeout(timer); resolve(null); });
    child.on("close", () => {
      clearTimeout(timer);
      try { resolve(JSON.parse(output) as BridgeResult<unknown>); } catch { resolve(null); }
    });
    child.stdin.end(JSON.stringify(request));
  });
}

const rootConfigured = () => !!process.env.ZWA_PORTAL_URL || rootPattern.test(process.env.ZWA_PRIVATE_EXCHANGE_ROOT ?? "");

/** Call one fixed exchange action as the authenticated participant. */
export async function exchangeAction<T>(action: string, payload: Record<string, unknown> = {}): Promise<BridgeResult<T> | null> {
  const session = await participant();
  if (!session || !rootConfigured()) return null;
  return await runPortal({ action, actor: session.id, role: session.role,
    sessionHash: sessionHash(session), payload }) as BridgeResult<T> | null;
}

/** Admit a new judge session for this sid (anonymous; caps enforced in the ledger). */
export async function startDemo(sid: string, clientHash: string): Promise<BridgeResult<{ accountId: string; expiresAt: number }> | null> {
  if (!demoMode() || !rootConfigured()) return null;
  return await runPortal({ action: "demo-start", actor: "anonymous", role: "anonymous",
    sessionHash: sessionHash({ sid }), payload: { clientHash } }) as BridgeResult<{ accountId: string; expiresAt: number }> | null;
}

const anonymousHash = createHash("sha256").update("ZWA_ANONYMOUS_MARKET_V1").digest("hex");

/** The dealer's public listings, readable without a session in demo mode. */
export async function publicMarket(): Promise<Market | null> {
  if (!demoMode() || !rootConfigured()) return null;
  const result = await runPortal({ action: "public-market", actor: "anonymous", role: "anonymous",
    sessionHash: anonymousHash, payload: {} }) as BridgeResult<Market> | null;
  return result?.ok ? result.data : null;
}

async function data<T>(action: string, payload: Record<string, unknown> = {}): Promise<T | null> {
  const result = await exchangeAction<T>(action, payload);
  return result?.ok ? result.data : null;
}

export const ownOrder = (id: string) => /^[0-9a-f]{32}$/.test(id) ? data<PrivateOrder>("order", { orderId: id }) : Promise.resolve(null);
export const ownOrders = async () => (await data<{ orders: PrivateOrder[] }>("orders"))?.orders ?? null;
export const market = async () => (await data<Market>("market")) ?? (await publicMarket());
export const walletStatus = () => data<WalletStatus>("wallet");
export const participantSession = participant;

/** Shared checks for every state-changing exchange route. */
export async function mutationAllowed(request: NextRequest): Promise<boolean> {
  if (!requestOrigin(request)) return false;
  if (Number(request.headers.get("content-length") || 0) > 2048) return false;
  const session = await currentSession();
  if (!session || !allow(`mutation|${session.sid}`, 20, 30)) return false;
  return csrfValid(request.headers.get("x-zwa-csrf"));
}

export async function readSmallJson(request: NextRequest): Promise<Record<string, unknown> | null> {
  const raw = await request.text();
  if (raw.length > 2048) return null;
  try {
    const value = JSON.parse(raw || "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : null;
  } catch { return null; }
}

export type TxCheck = { chainActive: boolean; found: boolean; included?: boolean; height?: number; blockHash?: string | null;
  confirmations?: number; tipHeight?: number; sizeBytes?: number; genesisHash?: string };

/** Read-only inclusion check of one txid on the demo chain. */
export async function verifyDemoTx(txid: string): Promise<TxCheck | null> {
  if (!demoMode() || !rootConfigured() || !/^[0-9a-f]{64}$/.test(txid)) return null;
  const result = await runPortal({ action: "verify-tx", actor: "anonymous", role: "anonymous",
    sessionHash: anonymousHash, payload: { txid } }) as BridgeResult<TxCheck> | null;
  return result?.ok ? result.data : null;
}
