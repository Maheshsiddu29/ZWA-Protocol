import catalog from "../../exchange/catalog.json";

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
/** Short display symbol per instrument. */
export const symbol: Record<string, string> = {
  "zwa-gold-reference": "ZGLD", "zwa-treasury-style": "ZTSY", "zwa-credit-participation": "ZCRD",
};
export const mark: Record<string, string> = {
  "zwa-gold-reference": "Au", "zwa-treasury-style": "Tb", "zwa-credit-participation": "Cr",
};
export const assetName = (id: string) => assetById(id)?.name ?? id;

/** 1 ZEC = 100,000,000 zatoshis. Display in ZEC with up to 8 decimals. */
export function zec(zat: number | null | undefined): string {
  if (zat == null) return "—";
  const negative = zat < 0;
  const value = Math.abs(zat);
  const whole = Math.floor(value / 1e8);
  const fraction = String(value % 1e8).padStart(8, "0").replace(/0+$/, "");
  return `${negative ? "−" : ""}${whole.toLocaleString("en-US")}${fraction ? `.${fraction}` : ""} ZEC`;
}
export const zatoshis = (zat: number) => `${zat.toLocaleString("en-US")} zatoshis`;
export const shortHash = (value: string, head = 10, tail = 6) => value.length > head + tail + 1 ? `${value.slice(0, head)}…${value.slice(-tail)}` : value;
