import "server-only";

import { lstat, readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { currentAccount, type Role } from "@/lib/exchange-auth";

type Holding = { instrumentId: string; assetBase: string; buyerUnits: number; sellerUnits: number };
type Receipt = { instrumentId: string; txid: string; height: number; paymentZat: number; approvalMode: string };
type Snapshot = {
  version: number; network: string; privacy: string; genesisHash: string;
  anchorHash: string; anchorHeight: number; scanHeight: number;
  buyerNativeZat: number; sellerNativeZat: number;
  holdings: Holding[]; receipts: Receipt[];
};

export type ParticipantPortfolio = {
  state: "CHAIN_ACTIVE" | "CHAIN_RESTARTED";
  scanHeight: number;
  nativeZat: number;
  holdings: { instrumentId: string; assetBase: string; units: number }[];
  receipts: Receipt[];
};

const hex64 = /^[0-9a-f]{64}$/;
const rootPattern = /^\/private\/tmp\/zwa4e-[A-Za-z0-9-]+$/;
const integer = (value: unknown): value is number => Number.isSafeInteger(value) && (value as number) >= 0;

function validSnapshot(value: unknown): value is Snapshot {
  if (!value || typeof value !== "object") return false;
  const data = value as Partial<Snapshot>;
  if (data.version !== 1 || data.network !== "regtest" || data.privacy !== "OPERATOR_PRIVATE_WALLET_SCAN" ||
      typeof data.genesisHash !== "string" || !hex64.test(data.genesisHash) ||
      typeof data.anchorHash !== "string" || !hex64.test(data.anchorHash) ||
      !integer(data.anchorHeight) || !integer(data.scanHeight) || !integer(data.buyerNativeZat) ||
      !integer(data.sellerNativeZat) || !Array.isArray(data.holdings) || !Array.isArray(data.receipts) ||
      data.holdings.length < 1 || data.holdings.length > 64 ||
      data.receipts.length < 1 || data.receipts.length > 1000) return false;
  const ids = new Set<string>(), bases = new Set<string>(), txids = new Set<string>();
  for (const item of data.holdings) {
    if (!item || typeof item.instrumentId !== "string" || !/^[a-z0-9-]{1,80}$/.test(item.instrumentId) ||
        typeof item.assetBase !== "string" || !hex64.test(item.assetBase) ||
        !integer(item.buyerUnits) || !integer(item.sellerUnits) ||
        ids.has(item.instrumentId) || bases.has(item.assetBase)) return false;
    ids.add(item.instrumentId); bases.add(item.assetBase);
  }
  for (const receipt of data.receipts) {
    if (!receipt || typeof receipt.instrumentId !== "string" || !ids.has(receipt.instrumentId) ||
        typeof receipt.txid !== "string" || !hex64.test(receipt.txid) || txids.has(receipt.txid) ||
        !integer(receipt.height) || !integer(receipt.paymentZat) ||
        !["SCRIPTED_TEST_MODE", "HUMAN_APPROVED"].includes(receipt.approvalMode) ||
        receipt.height > data.scanHeight) return false;
    txids.add(receipt.txid);
  }
  return true;
}

async function readSmallJson(file: string, limit: number): Promise<unknown> {
  const stat = await lstat(file);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size > limit) throw new Error("unsafe portfolio file");
  return JSON.parse(await readFile(file, "utf8"));
}

async function newestSnapshot(root: string): Promise<Snapshot | null> {
  const exchange = path.join(root, "exchange");
  const files = [path.join(exchange, "portfolio-after-restart.json")];
  try {
    const entries = await readdir(path.join(exchange, "rescans"), { withFileTypes: true });
    for (const entry of entries) {
      if (entry.isDirectory() && /^scan\.[A-Za-z0-9]{6}$/.test(entry.name))
        files.push(path.join(exchange, "rescans", entry.name, "portfolio.json"));
    }
  } catch { /* No subsequent rescan yet. */ }
  let newest: { time: number; data: Snapshot } | null = null;
  for (const file of files) {
    try {
      const stat = await lstat(file);
      const data = await readSmallJson(file, 65536);
      if (validSnapshot(data) && (!newest || stat.mtimeMs > newest.time)) newest = { time: stat.mtimeMs, data };
    } catch { /* A missing or malformed scan never grants holdings. */ }
  }
  return newest?.data ?? null;
}

async function rpc(port: number, method: string, params: unknown[] = []): Promise<unknown> {
  const response = await fetch(`http://127.0.0.1:${port}`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "1.0", id: "zwa-portfolio", method, params }),
    cache: "no-store", signal: AbortSignal.timeout(3000),
  });
  if (!response.ok) throw new Error("chain RPC unavailable");
  const data = await response.json();
  if (data?.error != null) throw new Error("chain RPC rejected portfolio check");
  return data?.result;
}

async function liveSnapshot(root: string, snapshot: Snapshot, port: number): Promise<"CHAIN_ACTIVE" | "CHAIN_RESTARTED" | null> {
  const identity = await readSmallJson(path.join(root, "chain-identity.json"), 8192) as Record<string, unknown>;
  if (identity.detectedReset || identity.genesisHash !== snapshot.genesisHash ||
      identity.anchorHash !== snapshot.anchorHash || identity.anchorHeight !== snapshot.anchorHeight ||
      !["CHAIN_ACTIVE", "CHAIN_RESTARTED"].includes(String(identity.lastStartState))) return null;
  const tip = await rpc(port, "getblockcount");
  if (!integer(tip) || tip !== snapshot.scanHeight) return null;
  const [genesis, anchor] = await Promise.all([
    rpc(port, "getblockhash", [0]), rpc(port, "getblockhash", [snapshot.anchorHeight]),
  ]);
  if (genesis !== snapshot.genesisHash || anchor !== snapshot.anchorHash) return null;
  for (const receipt of snapshot.receipts) {
    const block = await rpc(port, "getblock", [String(receipt.height), 1]) as { tx?: unknown };
    if (!Array.isArray(block?.tx) || !block.tx.includes(receipt.txid)) return null;
  }
  return identity.lastStartState as "CHAIN_ACTIVE" | "CHAIN_RESTARTED";
}

export async function participantPortfolio(role: "buyer" | "seller"): Promise<ParticipantPortfolio | null> {
  const account = await currentAccount();
  const expected = role === "buyer" ? process.env.ZWA_PORTFOLIO_BUYER_ID : process.env.ZWA_PORTFOLIO_SELLER_ID;
  if (!account || account.role !== (role as Role) || !expected || account.id !== expected) return null;
  const root = process.env.ZWA_PRIVATE_EXCHANGE_ROOT || "";
  const portText = process.env.ZWA_CHAIN_RPC_PORT || "";
  if (!rootPattern.test(root) || !/^[0-9]{4,5}$/.test(portText)) return null;
  const port = Number(portText);
  if (!Number.isSafeInteger(port) || port < 1024 || port > 65535) return null;
  try {
    const rootStat = await lstat(root);
    if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) return null;
    const snapshot = await newestSnapshot(root);
    if (!snapshot) return null;
    const state = await liveSnapshot(root, snapshot, port);
    if (!state) return null;
    return { state, scanHeight: snapshot.scanHeight,
      nativeZat: role === "buyer" ? snapshot.buyerNativeZat : snapshot.sellerNativeZat,
      holdings: snapshot.holdings.map(item => ({ instrumentId: item.instrumentId,
        assetBase: item.assetBase, units: role === "buyer" ? item.buyerUnits : item.sellerUnits })),
      receipts: snapshot.receipts };
  } catch { return null; }
}
