import Link from "next/link";
import { notFound } from "next/navigation";
import { ExchangeShell } from "@/components/exchange/ExchangeShell";
import { assetById, boundActiveAsset, categoryName, issuedCatalog } from "@/lib/exchange";
import { goldReference } from "@/lib/exchange-market";

export const dynamic = "force-dynamic";
export default async function AssetPage({ params }: { params: Promise<{ assetId: string }> }) {
  const { assetId } = await params;
  const asset = assetById(assetId);
  if (!asset) notFound();
  const active = await boundActiveAsset();
  const issued = (await issuedCatalog())?.get(assetId);
  const live = active?.asset.id === assetId ? active.checkout : null;
  const gold = assetId === "zwa-gold-reference" ? await goldReference() : null;
  return <ExchangeShell eyebrow={`ASSETS / ${categoryName[asset.category]}`} title={asset.name} intro={asset.description}>
    <div className="exchange-detail-grid"><div className="exchange-detail-main"><div className="exchange-art-panel"><span>{assetId === "zwa-gold-reference" ? "Au" : assetId === "zwa-treasury-style" ? "Tb" : "Cr"}</span><small>SYNTHETIC · REGTEST</small></div><section className="exchange-panel"><span className="exchange-eyebrow">UNDERLYING RIGHTS</span><h2>What this unit represents</h2><p>{asset.underlyingRights}</p><p className="exchange-muted">Rights evidence: none. This experimental unit is not backed, audited, redeemable, or a regulated investment.</p></section><section className="exchange-panel"><span className="exchange-eyebrow">TERMS & ACCESS</span><div className="exchange-facts"><div><span>Issuer</span><strong>{asset.issuerId} · disposable authority</strong></div><div><span>Authorization</span><strong>{live ? "Fresh Groth16 authorization in active checkout" : "No active authorization for this listing"}</strong></div><div><span>Unit / precision</span><strong>{asset.unit} / {asset.precision} decimals</strong></div><div><span>Investor policy</span><strong>{asset.permittedInvestors}</strong></div><div><span>Transfer policy</span><strong>{asset.transferRestrictions}</strong></div><div><span>Offer</span><strong>{asset.offerTerms}</strong></div><div><span>Redemption</span><strong>{asset.redemptionTerms}</strong></div><div><span>Policy reference</span><strong>{asset.compliancePolicy}</strong></div></div></section></div>
      <aside className="exchange-quote-panel"><span className="exchange-eyebrow">PRICE & AVAILABILITY</span><h2>{live ? "Executable local quote" : "Not available in demo"}</h2><p>{live ? "The wallet owners review this exact ZEC-denominated amount in their own terminals." : issued ? "Issuance and seller inventory were observed on this disposable chain, but no live checkout is bound to this listing." : "No currently observed issuance, inventory, and settlement session are bound to this listing."}</p>{live?.quote && <div className="exchange-price"><strong>{live.quote.paymentAmount.toLocaleString("en-US")}</strong><span>zatoshis total · {live.quote.quantity} units</span></div>}
        <div className="exchange-facts"><div><span>Pricing model</span><strong>{asset.pricingModel.replaceAll("_", " ")}</strong></div><div><span>Price source</span><strong>{asset.pricingSource}</strong></div>{gold && <><div><span>XAU/USD reference</span><strong>${gold.value} / troy oz</strong></div><div><span>Observed</span><strong>{gold.observedAt}</strong></div><div><span>Confidence</span><strong>± ${gold.confidence}</strong></div><div><span>Trust</span><strong>{gold.trust}</strong></div></>}{assetId === "zwa-gold-reference" && !gold && <div><span>XAU/USD reference</span><strong>Unavailable or stale</strong></div>}{issued && <><div><span>Issuance</span><strong>Experimental issued at height {issued.issuanceHeight}</strong></div><div><span>Issuance txid</span><strong className="exchange-hash">{issued.issuanceTxid}</strong></div><div><span>AssetBase</span><strong className="exchange-hash">{issued.assetBase}</strong></div><div><span>Seller inventory at issue</span><strong>{issued.inventoryUnitsAtIssue} units observed</strong></div></>}{live?.quote && <><div><span>Quote digest</span><strong className="exchange-hash">{live.quote.quoteDigest}</strong></div><div><span>Sponsor fee</span><strong>{live.quote.networkFee.toLocaleString("en-US")} zatoshis</strong></div></>}</div>
        {live ? <Link className="exchange-button" href="/demo#checkout">Review live checkout <span>↗</span></Link> : <span className="exchange-disabled">NOT AVAILABLE IN DEMO</span>}
        <small className="exchange-footnote">No browser wallet signing is available. Local role access is separate from wallet authorization. USD references never determine the ZEC debit in this version.</small>
      </aside></div>
  </ExchangeShell>;
}
