import "server-only";

import { spawn } from "node:child_process";
import path from "node:path";
import type { NextRequest } from "next/server";
import { csrfValid, currentSession, sessionHash } from "@/lib/exchange-auth";
import { localRequestOrigin } from "@/lib/local-origin";

const accountId = /^[a-zA-Z0-9_-]{1,80}$/;
const rootPattern = /^\/private\/tmp\/zwa4e-[A-Za-z0-9-]+$/;

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
};
export type MarketOffer = {
  offerId: string; instrumentId: string; name: string; issuerId: string; assetBase: string;
  issuanceTxid: string; issuanceHeight: number; availableUnits: number; inventoryVerifiedAtTip: boolean;
  priceZatPerUnit: number | null; priceClassification: string | null; priceVersion: number | null;
  sellerId: string | null;
};
export type Market = { chainActive: boolean; chainHeight?: number; instruments: MarketOffer[] };
export type WalletStatus = {
  paired: boolean; role: "buyer" | "seller"; approvalMode: string; method?: string;
  walletAddress?: string; pairedAt?: number; expiresAt?: number; companionOnline?: boolean;
};
export type BridgeResult<T> = { ok: true; data: T } | { ok: false; error: string };

async function participant() {
  const session = await currentSession();
  if (!session || !["buyer", "seller"].includes(session.role) || !accountId.test(session.id)) return null;
  const expected = session.role === "buyer" ? process.env.ZWA_PORTFOLIO_BUYER_ID : process.env.ZWA_PORTFOLIO_SELLER_ID;
  if (!expected || session.id !== expected) return null;
  return session;
}

function runPortal(request: object): Promise<BridgeResult<unknown> | null> {
  const project = path.resolve(process.cwd(), "..");
  return new Promise((resolve) => {
    const child = spawn("python3", ["-m", "exchange.portal"], {
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

/** Call one fixed exchange action as the authenticated buyer or seller. */
export async function exchangeAction<T>(action: string, payload: Record<string, unknown> = {}): Promise<BridgeResult<T> | null> {
  const session = await participant();
  if (!session || !rootPattern.test(process.env.ZWA_PRIVATE_EXCHANGE_ROOT ?? "")) return null;
  return await runPortal({ action, actor: session.id, role: session.role,
    sessionHash: sessionHash(session), payload }) as BridgeResult<T> | null;
}

async function data<T>(action: string, payload: Record<string, unknown> = {}): Promise<T | null> {
  const result = await exchangeAction<T>(action, payload);
  return result?.ok ? result.data : null;
}

export const ownOrder = (id: string) => /^[0-9a-f]{32}$/.test(id) ? data<PrivateOrder>("order", { orderId: id }) : Promise.resolve(null);
export const ownOrders = async () => (await data<{ orders: PrivateOrder[] }>("orders"))?.orders ?? null;
export const market = () => data<Market>("market");
export const walletStatus = () => data<WalletStatus>("wallet");

/** Shared checks for every state-changing exchange route. */
export async function mutationAllowed(request: NextRequest): Promise<boolean> {
  if (!localRequestOrigin(request)) return false;
  if (Number(request.headers.get("content-length") || 0) > 2048) return false;
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
