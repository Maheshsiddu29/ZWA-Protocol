import Link from "next/link";
import { assets, categoryName, mark, symbol, zec } from "@/lib/exchange";
import type { Market } from "@/lib/private-orders";

/** Dealer listings from the backend; catalog entries without a live offer say so. */
export function MarketTable({ market }: { market: Market | null }) {
  const live = market?.chainActive ? market.instruments : [];
  return <div className="market" role="table" aria-label="Markets">
    <div className="market-row market-head" role="row"><span role="columnheader">Asset</span><span role="columnheader">Price per unit</span><span role="columnheader">Available</span><span /></div>
    {assets.map(asset => {
      const offer = live.find(item => item.instrumentId === asset.id);
      return <Link className="market-row" role="row" href={`/exchange/${asset.id}`} key={asset.id}>
        <span className="market-name" role="cell"><span className="mark">{mark[asset.id]}</span><span><strong>{asset.name}</strong><small>{symbol[asset.id]} · {categoryName[asset.category]}</small></span></span>
        <span className="num" role="cell">{offer?.priceZatPerUnit ? zec(offer.priceZatPerUnit) : "—"}<small>{offer?.priceZatPerUnit ? `${offer.priceZatPerUnit.toLocaleString("en-US")} zatoshis` : "No live price"}</small></span>
        <span className="num" role="cell">{offer ? `${offer.availableUnits.toLocaleString("en-US")} units` : "—"}<small>{offer?.inventoryVerifiedAtTip ? `Scanned at block ${offer.inventoryScanHeight?.toLocaleString("en-US")}` : market?.chainActive === false ? "Chain unavailable" : "Not listed"}</small></span>
        <span className="go" role="cell"><span className="button button-small button-outline">{offer ? "Trade" : "Details"}</span></span>
      </Link>;
    })}
  </div>;
}
