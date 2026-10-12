import "server-only";

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import path from "node:path";
import { currentAccount } from "@/lib/exchange-auth";

const run = promisify(execFile);
const recordId = /^[0-9a-f]{32}$/;
const accountId = /^[a-zA-Z0-9_-]{1,80}$/;
export type PrivateOrder = {
  id: string; state: string; buyerId: string; sellerId: string;
  instrumentId: string; assetBase: string; quantity: number;
  quote: null | { digest: string; assetBase: string; quantity: number; paymentZat: number;
                   networkFeeZat: number; expiryHeight: number };
  events: { sequence: number; from_state: string | null; to_state: string; occurred_at: number }[];
  proofVerified: boolean; buyerApprovalMode: string | null; sellerApprovalMode: string | null;
  chainActive: boolean; chainHeight: number | null;
  receipt: null | { txid: string; height: number; blockHash: string; paymentZat: number;
                    quantity: number; approvalMode: string; quoteDigest: string };
};
export type PrivateOffer = { id: string; instrumentId: string; assetBase: string; availableUnits: number };

async function allowedActor(): Promise<{ id: string; role: "buyer" | "seller" } | null> {
  const account = await currentAccount();
  if (!account || !["buyer", "seller"].includes(account.role) || !accountId.test(account.id)) return null;
  const expected = account.role === "buyer" ? process.env.ZWA_PORTFOLIO_BUYER_ID : process.env.ZWA_PORTFOLIO_SELLER_ID;
  if (!expected || account.id !== expected) return null;
  return { id: account.id, role: account.role as "buyer" | "seller" };
}

async function call(action: "get" | "list" | "offers", actor: string, id?: string): Promise<unknown> {
  const root = process.env.ZWA_PRIVATE_EXCHANGE_ROOT;
  if (root !== "/private/tmp/zwa4e-phase4e-b") return null;
  const project = path.resolve(process.cwd(), "..");
  const args = ["-m", "exchange.order_view", action, actor];
  if (id) args.push(id);
  try {
    const { stdout } = await run("python3", args, {
      cwd: project, env: { ...process.env, PYTHONPATH: project },
      timeout: 15000, maxBuffer: 131072,
    });
    return JSON.parse(stdout);
  } catch { return null; }
}

export async function ownOrder(id: string): Promise<PrivateOrder | null> {
  const actor = await allowedActor();
  if (!actor || !recordId.test(id)) return null;
  const data = await call("get", actor.id, id) as PrivateOrder | null;
  if (!data || data.id !== id || ![data.buyerId, data.sellerId].includes(actor.id)) return null;
  return data;
}

export async function ownOrders(): Promise<PrivateOrder[] | null> {
  const actor = await allowedActor();
  if (!actor) return null;
  const data = await call("list", actor.id) as { orders?: PrivateOrder[] } | null;
  return Array.isArray(data?.orders) ? data.orders : null;
}

export async function availableOffers(): Promise<{ offers: PrivateOffer[]; chainActive: boolean; chainHeight?: number } | null> {
  const actor = await allowedActor();
  return actor ? call("offers", actor.id) as Promise<{ offers: PrivateOffer[]; chainActive: boolean; chainHeight?: number } | null> : null;
}

export async function mutateOrder(action: "create-offer" | "request" | "cancel", payload: unknown): Promise<unknown | null> {
  const actor = await allowedActor();
  if (!actor || (action === "create-offer" ? actor.role !== "seller" : actor.role !== "buyer")) return null;
  const root = process.env.ZWA_PRIVATE_EXCHANGE_ROOT;
  if (root !== "/private/tmp/zwa4e-phase4e-b" || !payload || typeof payload !== "object") return null;
  const encoded = JSON.stringify(payload);
  if (encoded.length > 2048) return null;
  const project = path.resolve(process.cwd(), "..");
  try {
    const { stdout } = await run("python3", ["-m", "exchange.order_mutation", action,
      actor.role, actor.id, encoded], {
      cwd: project, env: { ...process.env, PYTHONPATH: project },
      timeout: 15000, maxBuffer: 16384,
    });
    return JSON.parse(stdout);
  } catch { return null; }
}
