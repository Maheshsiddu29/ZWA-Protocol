import Link from "next/link";
import { Protected } from "@/components/exchange/Protected";
import { boundActiveAsset } from "@/lib/exchange";

export const dynamic = "force-dynamic";
export default async function PortfolioPage() {
  const active = await boundActiveAsset();
  const scan = active?.checkout.walletScans;
  const receipt = active?.checkout.receipt;
  return <Protected role="buyer" eyebrow="BUYER / PORTFOLIO" title="Local wallet portfolio." intro="Holdings appear only after the independent buyer wallet scan is reconciled with an observed transaction and exact AssetBase.">
    {active && scan && receipt ? <div className="exchange-holding"><div><span className="exchange-eyebrow">OBSERVED REGTEST HOLDING</span><h2>{active.asset.name}</h2><p>AssetBase <code>{active.checkout.quote?.assetId}</code></p></div><div><strong>{scan.buyerAfter.assetUnits.toLocaleString("en-US")}</strong><span>units in local demo buyer wallet</span></div><div><strong>{scan.buyerAfter.nativeZat.toLocaleString("en-US")}</strong><span>native zatoshis after trade</span></div><Link href="/exchange/activity">View receipt ↗</Link></div> : <div className="exchange-empty"><h2>No verified local holding to display.</h2><p>Start a fresh operator-run checkout and wait for wallet reconciliation. Catalog entries and old recorded receipts do not create holdings.</p><Link href="/exchange/assets">Browse assets ↗</Link></div>}
    <p className="exchange-caveat">This role session is local portal access. The wallet signer separately controls its own keys and approves every purchase. Current holdings are not a production custody statement.</p>
  </Protected>;
}
