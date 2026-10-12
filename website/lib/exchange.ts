import catalog from "../../exchange/catalog.json";
import { lstatSync, readFileSync } from "node:fs";

export type Asset = (typeof catalog)[number];
export const assets: Asset[] = catalog;
export const assetById = (id: string) => assets.find((asset) => asset.id === id);
export const categoryName: Record<string, string> = {
  PRECIOUS_METALS: "Precious metals", COMMODITIES: "Commodities", TREASURY_BILLS: "Treasury bills",
  GOVERNMENT_BONDS: "Government bonds", CORPORATE_BONDS: "Corporate bonds",
  MONEY_MARKET_FUNDS: "Money-market funds", INVESTMENT_FUNDS: "Investment funds",
  REAL_ESTATE: "Real estate", PRIVATE_CREDIT: "Private credit", EQUITIES: "Equities",
  TRADE_FINANCE_RECEIVABLES: "Trade finance", OTHER_RWA: "Other RWAs",
};

export type CheckoutView = {
  sessionId: string; status: string | null;
  approvalEvidence?: string;
  availability?: { state: string; tipHeight: number | null };
  quote?: { assetId: string; quantity: number; paymentAmount: number; networkFee: number; maximumTotalDebit: number; quoteDigest: string; network: string };
  authorization?: { issuanceProvenance: string; investorEligibility: string; exactTradeAuthorization: string };
  receipt?: { txid: string; confirmations: number | null; height: number; finality: string };
  walletScans?: { buyerBefore: { assetUnits: number; nativeZat: number }; buyerAfter: { assetUnits: number; nativeZat: number }; sellerBefore: { assetUnits: number; nativeZat: number }; sellerAfter: { assetUnits: number; nativeZat: number } };
};

export async function activeCheckout(): Promise<CheckoutView | null> {
  const origin = process.env.ZWA_CHECKOUT_URL || "http://127.0.0.1:18745";
  const url = new URL(origin);
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(url.hostname) || url.pathname !== "/" || url.search || url.hash) return null;
  try {
    const response = await fetch(`${url.origin}/v1/checkout/active`, { cache: "no-store", signal: AbortSignal.timeout(2000) });
    if (!response.ok) return null;
    return await response.json() as CheckoutView;
  } catch { return null; }
}

export async function boundActiveAsset(): Promise<{ asset: Asset; checkout: CheckoutView } | null> {
  const selected = process.env.ZWA_ACTIVE_INSTRUMENT_ID;
  const asset = selected ? assetById(selected) : undefined;
  if (!asset) return null;
  const checkout = await activeCheckout();
  if (!checkout?.quote || checkout.availability?.state !== "AVAILABLE" || checkout.quote.network !== "regtest" ||
      checkout.authorization?.issuanceProvenance !== "VERIFIED" ||
      checkout.authorization?.investorEligibility !== "VERIFIED" ||
      checkout.authorization?.exactTradeAuthorization !== "VERIFIED") return null;
  const issued = await issuedCatalog(checkout);
  if (issued?.get(asset.id)?.assetBase !== checkout.quote.assetId) return null;
  return { asset, checkout };
}

export type IssuedAsset = { assetBase: string; issuanceTxid: string; issuanceHeight: number; inventoryUnitsAtIssue: number };

export async function issuedCatalog(checkoutInput?: CheckoutView | null): Promise<Map<string, IssuedAsset> | null> {
  const checkout = checkoutInput === undefined ? await activeCheckout() : checkoutInput;
  if (!checkout || checkout.availability?.state !== "AVAILABLE" || checkout.status === null) return null;
  const filename = process.env.ZWA_EXCHANGE_REGISTRY_PATH || "";
  if (!/^\/private\/tmp\/zwa4d-[A-Za-z0-9-]+\/exchange\/instrument-registry\.json$/.test(filename)) return null;
  try {
    const stat = lstatSync(filename);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 32768) return null;
    const data = JSON.parse(readFileSync(filename, "utf8"));
    if (data?.version !== 1 || data?.network !== "regtest" || data?.trustSource !== "EXPERIMENTAL_LOCAL_OBSERVATION" || !Array.isArray(data.instruments) || data.instruments.length !== assets.length) return null;
    const result = new Map<string, IssuedAsset>();
    const bases = new Set<string>();
    for (const record of data.instruments) {
      if (!assetById(record.id) || result.has(record.id) || record.availability !== "EXPERIMENTAL_TRADABLE" ||
          !/^[0-9a-f]{64}$/.test(record.asset_base) || !/^[0-9a-f]{64}$/.test(record.issuance_txid) ||
          !Number.isInteger(record.issuance_height) || record.issuance_height <= 0 ||
          !Number.isInteger(record.inventory_scan_height) || record.inventory_scan_height < record.issuance_height ||
          !Number.isInteger(record.seller_inventory_units) || record.seller_inventory_units <= 0 ||
          bases.has(record.asset_base)) return null;
      bases.add(record.asset_base);
      result.set(record.id, { assetBase: record.asset_base, issuanceTxid: record.issuance_txid,
                              issuanceHeight: record.issuance_height, inventoryUnitsAtIssue: record.seller_inventory_units });
    }
    return result;
  } catch { return null; }
}
