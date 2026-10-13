import type { Metadata } from "next";
import Link from "next/link";
import { DealerPolicy } from "@/components/exchange/DealerPolicy";
import { OrderList } from "@/components/exchange/OrderList";
import { SellerListing } from "@/components/exchange/SellerListing";
import { SellerPricing } from "@/components/exchange/SellerPricing";
import { assetName, zec } from "@/lib/exchange";
import { csrfToken, currentSession, demoMode } from "@/lib/exchange-auth";
import { participantPortfolio } from "@/lib/persistent-portfolio";
import { market, ownOrders, walletStatus } from "@/lib/private-orders";

export const metadata: Metadata = { title: "Seller desk" };
export const dynamic = "force-dynamic";

const SignOut = () => <form action="/api/exchange/logout" method="post"><button className="link-button quiet" type="submit">Sign out</button></form>;

export default async function SellerPage() {
  const session = await currentSession();
  if (!session || session.role !== "seller") return <section className="page"><div className="shell">
    <header className="page-head"><div><p className="eyebrow">Seller</p><h1>Seller desk</h1>
      <p className="lede">{demoMode() ? "The demo dealer lists inventory, sets prices and receives proceeds. Its wallet signs sales under a standing policy." : "List inventory, set prices and approve sales from your own wallet."}</p></div></header>
    <div className="empty"><h2>Seller sign-in required</h2><p>The seller desk is available to the provisioned seller account{demoMode() ? " that operates the demo dealer" : ""}.</p>
      <Link className="button button-blue" href="/sign-in">Sign in</Link></div>
  </div></section>;

  const csrf = csrfToken(session);
  const [orders, listing, desk] = await Promise.all([ownOrders(), market(), walletStatus()]);
  const offers = (listing?.instruments ?? []).filter(item => item.sellerId === session.id);
  const pricing = offers.map(o => ({ offerId: o.offerId, name: o.name, availableUnits: o.availableUnits, priceZatPerUnit: o.priceZatPerUnit }));
  const open = (orders ?? []).filter(o => !["RECEIPT_AVAILABLE", "CANCELLED", "EXPIRED", "REJECTED", "EXECUTION_FAILED"].includes(o.state));
  const proceeds = (orders ?? []).filter(o => o.receipt).reduce((sum, o) => sum + (o.receipt?.paymentZat ?? 0), 0);

  if (desk?.mode === "DEMO_DEALER_STANDING_POLICY") return <section className="page"><div className="shell">
    <header className="page-head"><div><p className="eyebrow">Seller · {session.id}</p><h1>Dealer desk</h1>
      <p className="lede">Inventory and proceeds come from scans of the dealer wallet. Sales are signed automatically, but only inside the standing policy below.</p></div>
      <div className="actions"><SignOut /></div></header>
    <div className="metrics">
      <div><span>Wallet ZEC</span><strong>{zec(desk.nativeZat)}</strong></div>
      <div><span>Proceeds from settled sales</span><strong>{zec(proceeds)}</strong></div>
      <div><span>Orders in progress</span><strong>{open.length}</strong></div>
    </div>
    <div className="split" style={{ marginTop: 20 }}>
      <div className="stack">
        <div className="panel">
          <p className="label">Inventory</p>
          <div className="list" style={{ marginTop: 12 }}>{(desk.holdings ?? []).map(h => <div className="list-row" key={h.assetBase}>
            <span><strong>{assetName(h.instrumentId)}</strong><small>Scanned at block {h.scanHeight.toLocaleString("en-US")}</small></span>
            <span className="hide-sm" /><span className="hide-sm" /><span className="num">{h.units.toLocaleString("en-US")} units</span></div>)}</div>
          {!desk.holdings?.length && <p className="muted small">No current inventory scan.</p>}
        </div>
        {pricing.length > 0 && <SellerPricing offers={pricing} csrf={csrf} />}
      </div>
      <aside className="panel panel-muted">
        <p className="label">Standing policy</p>
        <DealerPolicy initial={desk.policy ?? "ACTIVE"} csrf={csrf} />
        <dl className="facts" style={{ marginTop: 14 }}>
          <div><dt>Signs only</dt><dd>Listed assets at your current price, to a live demo wallet, with proceeds to this wallet</dd></div>
          <div><dt>Per order</dt><dd>Up to {desk.limits?.maxUnitsPerOrder} units and {zec(desk.limits?.maxPaymentZatPerOrder)}</dd></div>
          <div><dt>Sponsored fees</dt><dd>{desk.sponsoredLastHour} of {desk.limits?.maxSponsoredOrdersPerHour} this hour</dd></div>
          <div><dt>Signer</dt><dd>{desk.signerOnline ? "Online" : "Offline: new orders wait"}</dd></div>
          <div><dt>Address</dt><dd className="hash">{desk.walletAddress}</dd></div>
        </dl>
      </aside>
    </div>
    <div className="section-title"><h2>Orders</h2></div>
    <OrderList orders={orders ?? []} empty="No orders yet." />
  </div></section>;

  // Owner mode: the seller approves each sale in their own wallet companion.
  const portfolio = await participantPortfolio("seller");
  return <section className="page"><div className="shell">
    <header className="page-head"><div><p className="eyebrow">Seller · {session.id}</p><h1>Seller desk</h1>
      <p className="lede">Your wallet controls its inventory and approves each sale in its own terminal. This page never signs.</p></div>
      <div className="actions"><Link className="button button-outline button-small" href="/wallet">Wallet pairing</Link><SignOut /></div></header>
    <div className="metrics">
      <div><span>Wallet ZEC</span><strong>{zec(portfolio?.nativeZat)}</strong></div>
      <div><span>Proceeds from settled sales</span><strong>{zec(proceeds)}</strong></div>
      <div><span>Orders in progress</span><strong>{open.length}</strong></div>
    </div>
    <div className="stack" style={{ marginTop: 20 }}>
      {portfolio && <SellerListing csrf={csrf} scanHeight={portfolio.scanHeight}
        holdings={portfolio.holdings.filter(h => h.units > 0 && !offers.some(o => o.instrumentId === h.instrumentId))
          .map(h => ({ instrumentId: h.instrumentId, name: assetName(h.instrumentId), units: h.units }))} />}
      {pricing.length > 0 && <SellerPricing offers={pricing} csrf={csrf} />}
      {!portfolio && <p className="notice notice-warn">No current wallet scan. Inventory appears once the chain and a fresh scan agree.</p>}
    </div>
    <div className="section-title"><h2>Orders</h2></div>
    <OrderList orders={orders ?? []} empty="No orders yet." />
  </div></section>;
}
