import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { BuyPanel } from "@/components/exchange/BuyPanel";
import { assetById, categoryName, mark, symbol } from "@/lib/exchange";
import { goldReference } from "@/lib/exchange-market";
import { csrfToken, currentSession, demoMode } from "@/lib/exchange-auth";
import { market, walletStatus } from "@/lib/private-orders";

export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: { params: Promise<{ assetId: string }> }): Promise<Metadata> {
  const asset = assetById((await params).assetId);
  return { title: asset?.name ?? "Asset" };
}

export default async function AssetPage({ params }: { params: Promise<{ assetId: string }> }) {
  const { assetId } = await params;
  const asset = assetById(assetId);
  if (!asset) notFound();
  const session = await currentSession();
  const [listing, wallet, gold] = await Promise.all([market(), session ? walletStatus() : null,
    assetId === "zwa-gold-reference" ? goldReference() : null]);
  const offer = listing?.chainActive ? listing.instruments.find(item => item.instrumentId === assetId) ?? null : null;
  const viewer = session ? { kind: session.kind, role: session.role } : null;
  return <section className="page"><div className="shell">
    <p className="small"><Link href="/exchange" className="link-button quiet">← Markets</Link></p>
    <div className="split" style={{ marginTop: 18 }}>
      <div className="stack">
        <div className="asset-title"><span className="mark mark-lg">{mark[asset.id]}</span>
          <div><p className="eyebrow">{symbol[asset.id]} · {categoryName[asset.category]}</p><h1 style={{ fontSize: "clamp(34px,4vw,54px)", margin: "10px 0 0" }}>{asset.name}</h1></div></div>
        <p className="lede muted" style={{ fontSize: 17, margin: "8px 0 4px" }}>{asset.description}</p>
        <div className="panel">
          <p className="label">Terms</p>
          <dl className="facts">
            <div><dt>What you hold</dt><dd>{asset.underlyingRights}</dd></div>
            <div><dt>Issuer</dt><dd>Demo issuer (a disposable test authority)</dd></div>
            <div><dt>Unit</dt><dd>Whole units, no decimals</dd></div>
            <div><dt>Who may buy</dt><dd>Holders of the demo eligibility credential. Each order proves it in zero knowledge.</dd></div>
            <div><dt>Transfers</dt><dd>Checked for trades through this exchange. Transfers outside the exchange are not restricted.</dd></div>
            <div><dt>Redemption</dt><dd>{asset.redemptionTerms}</dd></div>
            <div><dt>Settlement</dt><dd>Native ZEC, atomically with the asset, on the demo chain. The network fee is paid by the exchange.</dd></div>
          </dl>
        </div>
        {gold && <div className="panel panel-muted">
          <p className="label">Reference only</p>
          <dl className="facts">
            <div><dt>XAU/USD</dt><dd>${gold.value} per troy ounce (± ${gold.confidence})</dd></div>
            <div><dt>Source</dt><dd>{gold.source}, observed {new Date(gold.observedAt).toUTCString()}</dd></div>
          </dl>
          <p className="small muted">Shown for context. It does not set the ZEC price of this unit.</p>
        </div>}
        {offer && <details className="tech">
          <summary>Technical details</summary>
          <dl className="facts">
            <div><dt>AssetBase</dt><dd className="hash">{offer.assetBase}</dd></div>
            <div><dt>Issuance transaction</dt><dd className="hash">{offer.issuanceTxid}</dd></div>
            <div><dt>Issued at block</dt><dd>{offer.issuanceHeight.toLocaleString("en-US")}</dd></div>
            <div><dt>Price type</dt><dd>Fixed by the seller (version {offer.priceVersion}). Not an oracle price.</dd></div>
          </dl>
        </details>}
      </div>
      <aside className="buy-box">
        <BuyPanel offer={offer} viewer={viewer} account={session?.id ?? ""} wallet={wallet} demo={demoMode()} limits={listing?.demo ?? null}
                  csrf={session ? csrfToken(session) : ""} chainActive={listing?.chainActive ?? false} />
      </aside>
    </div>
  </div></section>;
}
