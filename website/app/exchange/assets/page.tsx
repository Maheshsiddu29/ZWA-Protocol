import Link from "next/link";
import { ExchangeShell } from "@/components/exchange/ExchangeShell";
import { assets, categoryName, boundActiveAsset, issuedCatalog } from "@/lib/exchange";

export const dynamic = "force-dynamic";
export default async function AssetsPage() {
  const active = await boundActiveAsset();
  const issued = await issuedCatalog();
  return <ExchangeShell eyebrow="02 / Assets" title="Explore instruments." intro="Each listing shows its actual availability and the source of any pricing reference. No catalog entry represents an externally verified asset.">
    <div className="exchange-list">{assets.map((asset, index)=><Link className="exchange-list-row" href={`/exchange/assets/${asset.id}`} key={asset.id}><span className="exchange-list-number">0{index+1}</span><div><span className="exchange-eyebrow">{categoryName[asset.category]}</span><h2>{asset.name}</h2><p>{asset.description}</p></div><div className="exchange-list-meta"><strong>{active?.asset.id===asset.id ? "Live session" : issued?.has(asset.id) ? "Experimental issued" : "Catalog only"}</strong><small>{asset.pricingModel.replaceAll("_", " ").toLowerCase()}</small></div><span aria-hidden>↗</span></Link>)}</div>
  </ExchangeShell>;
}
