import Link from "next/link";
import { Protected } from "@/components/exchange/Protected";
import { assetById } from "@/lib/exchange";
import { participantPortfolio } from "@/lib/persistent-portfolio";

export const dynamic = "force-dynamic";
export default async function PortfolioPage() {
  const portfolio = await participantPortfolio("buyer");
  return <Protected role="buyer" eyebrow="BUYER / PORTFOLIO" title="Local wallet portfolio." intro="Holdings come from the independent buyer wallet scan on the current anchored experimental chain.">
    {portfolio ? <>
      <p className="exchange-caveat">{portfolio.holdings.length} assets scanned at chain height {portfolio.scanHeight.toLocaleString("en-US")} · {portfolio.state.replaceAll("_", " ")}. Native balance: {portfolio.nativeZat.toLocaleString("en-US")} zatoshis.</p>
      {portfolio.holdings.map(holding => <div className="exchange-holding" key={holding.assetBase}>
        <div><span className="exchange-eyebrow">OBSERVED REGTEST HOLDING</span><h2>{assetById(holding.instrumentId)?.name ?? holding.instrumentId}</h2><p>AssetBase <code>{holding.assetBase}</code></p>
          {portfolio.receipts.filter(receipt => receipt.instrumentId === holding.instrumentId).map(receipt => <p key={receipt.txid}>Purchase tx <code>{receipt.txid}</code> · {receipt.paymentZat.toLocaleString("en-US")} zatoshis · {receipt.approvalMode.replaceAll("_", " ").toLowerCase()}</p>)}
        </div><div><strong>{holding.units.toLocaleString("en-US")}</strong><span>units in this local buyer wallet</span></div><Link href="/exchange/orders">View order history ↗</Link>
      </div>)}
    </> : <div className="exchange-empty"><h2>No verified local holding to display.</h2><p>A current anchored chain and fresh independent wallet scan are required.</p><Link href="/exchange/assets">Browse assets ↗</Link></div>}
    <p className="exchange-caveat">This role session grants portal access only. Wallet approval is a separate signer action. Scripted test transactions do not demonstrate human consent or production custody.</p>
  </Protected>;
}
