import Link from "next/link";
import { ExchangeShell } from "@/components/exchange/ExchangeShell";
import { assets, categoryName, boundActiveAsset, issuedCatalog } from "@/lib/exchange";

export const dynamic = "force-dynamic";
export default async function ExchangePage() {
  const active = await boundActiveAsset();
  const issued = await issuedCatalog();
  return <ExchangeShell eyebrow="01 / Exchange" title="Own the position. Understand the proof." intro="Explore distinct synthetic instruments, inspect their pricing and authorization, and follow native ZEC settlement on disposable regtest.">
    <section className="exchange-feature"><div><span className="exchange-eyebrow">LIVE CAPABILITY</span><h2>{active ? "One local checkout is active" : "Browse the experimental catalog"}</h2><p>{active ? active.checkout.receipt ? `${active.asset.name} has a current regtest receipt and reconciled wallet scans. ${active.checkout.approvalEvidence === "SCRIPTED_TEST_MODE" ? "This was a scripted test authorization." : "Wallet approval occurred in independent local signer processes."}` : `${active.asset.name} has a current Groth16-gated local session. Its exact ZEC payment requires independent wallet approval.` : "Catalog entries are product definitions. They become issued only after an observed regtest issuance and become purchasable only through a fresh, verified local wallet session."}</p><Link className="exchange-button" href={active ? `/exchange/assets/${active.asset.id}` : "/exchange/assets"}>{active ? "Inspect active instrument" : "Browse assets"} <span>↗</span></Link></div><div className="exchange-feature-art"><span>GOLD</span><span>TREASURY</span><span>CREDIT</span></div></section>
    <div className="exchange-section-heading"><div><span className="exchange-eyebrow">INSTRUMENTS</span><h2>Built for different markets.</h2></div><Link href="/exchange/assets">View all assets ↗</Link></div>
    <div className="exchange-card-grid">{assets.map((asset, index)=><Link className="exchange-card" href={`/exchange/assets/${asset.id}`} key={asset.id}><span className="exchange-card-index">0{index+1} / {categoryName[asset.category]}</span><div className={`exchange-mark exchange-mark-${index}`}>{index===0?"Au":index===1?"Tb":"Cr"}</div><h3>{asset.name}</h3><p>{asset.description}</p><div className="exchange-card-foot"><span>{active?.asset.id===asset.id ? "LIVE LOCAL SESSION" : issued?.has(asset.id) ? "EXPERIMENTAL ISSUED" : "CATALOG ONLY"}</span><span>Explore ↗</span></div></Link>)}</div>
    <p className="exchange-caveat">These are disposable experimental ZSAs. They convey no gold, Treasury, credit, property, fund, or equity rights. Market references are separate from executable native ZEC quotes.</p>
  </ExchangeShell>;
}
