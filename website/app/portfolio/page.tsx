import type { Metadata } from "next";
import Link from "next/link";
import { OrderList } from "@/components/exchange/OrderList";
import { TryDemoButton } from "@/components/exchange/TryDemoButton";
import { assetName, assets, zec } from "@/lib/exchange";
import { currentSession, demoMode } from "@/lib/exchange-auth";
import { participantPortfolio } from "@/lib/persistent-portfolio";
import { ownOrders, walletStatus } from "@/lib/private-orders";

export const metadata: Metadata = { title: "Portfolio" };
export const dynamic = "force-dynamic";

export default async function PortfolioPage() {
  const session = await currentSession();
  if (!session || session.role !== "buyer") return <section className="page"><div className="shell">
    <header className="page-head"><div><p className="eyebrow">Portfolio</p><h1>Portfolio</h1><p className="lede">Holdings and receipts for your wallet.</p></div></header>
    <div className="empty"><h2>{session ? "Portfolios belong to buyers" : "Nothing here yet"}</h2>
      <p>{session ? "The seller's inventory and proceeds are on the seller desk." : demoMode() ? "Start a demo to get a funded wallet and make a purchase." : "Sign in as a buyer to see holdings."}</p>
      {session ? <Link className="button button-blue" href="/seller">Seller desk</Link> : demoMode() ? <TryDemoButton /> : <Link className="button button-blue" href="/sign-in">Sign in</Link>}</div>
  </div></section>;

  const orders = (await ownOrders()) ?? [];
  let native: number | null = null, scanHeight: number | null = null;
  let holdings: { instrumentId: string; units: number }[] = [];
  let state = "";
  if (session.kind === "demo") {
    const wallet = await walletStatus();
    native = wallet?.nativeZat ?? null; scanHeight = wallet?.scanHeight ?? null; state = wallet?.state ?? "";
    holdings = (wallet?.holdings ?? []).map(h => ({ instrumentId: h.instrumentId, units: h.units }));
  } else {
    const portfolio = await participantPortfolio("buyer");
    if (portfolio) { native = portfolio.nativeZat; scanHeight = portfolio.scanHeight; state = "READY";
      holdings = portfolio.holdings.map(h => ({ instrumentId: h.instrumentId, units: h.units })); }
  }
  const settled = orders.filter(order => order.state === "RECEIPT_AVAILABLE");
  const held = holdings.filter(h => h.units > 0);
  return <section className="page"><div className="shell">
    <header className="page-head"><div><p className="eyebrow">Portfolio</p><h1>Portfolio</h1>
      <p className="lede">Balances come from scans of your wallet with its own keys. Receipts are checked against the live chain.</p></div>
      <div className="actions"><Link className="button button-blue" href="/exchange">Buy assets</Link></div></header>
    {state && state !== "READY" ? <p className="notice">Your wallet is still being prepared. <Link href="/wallet">See progress</Link>.</p> : scanHeight === null &&
      <p className="notice notice-warn">No current wallet scan is available. Holdings appear once the chain and a fresh scan agree.</p>}
    <div className="metrics" style={{ margin: "8px 0 0" }}>
      <div><span>ZEC balance</span><strong>{zec(native)}</strong></div>
      <div><span>Assets held</span><strong>{held.reduce((sum, h) => sum + h.units, 0).toLocaleString("en-US")} units</strong></div>
      <div><span>Settled purchases</span><strong>{settled.length}</strong></div>
    </div>
    <div className="section-title"><h2>Holdings</h2>{scanHeight !== null && <span className="small muted">Scanned at block {scanHeight.toLocaleString("en-US")}</span>}</div>
    <div className="list">{assets.map(asset => {
      const units = holdings.find(h => h.instrumentId === asset.id)?.units ?? 0;
      return <Link className="list-row" key={asset.id} href={`/exchange/${asset.id}`}>
        <span><strong>{assetName(asset.id)}</strong><small>{settled.filter(o => o.instrumentId === asset.id).length} purchase(s)</small></span>
        <span className="hide-sm" /><span className="hide-sm" /><span className="num">{scanHeight === null ? "—" : `${units} units`}</span>
      </Link>;
    })}</div>
    <div className="section-title"><h2>Orders</h2></div>
    <OrderList orders={orders} empty="No orders yet." />
  </div></section>;
}
