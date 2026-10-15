import type { Metadata } from "next";
import { MarketTable } from "@/components/exchange/MarketTable";
import { TryDemoButton } from "@/components/exchange/TryDemoButton";
import Link from "next/link";
import { zec } from "@/lib/exchange";
import { currentSession, demoMode } from "@/lib/exchange-auth";
import { market, walletStatus } from "@/lib/private-orders";

export const metadata: Metadata = { title: "Markets" };
export const dynamic = "force-dynamic";

export default async function ExchangePage() {
  const [listing, session] = await Promise.all([market(), currentSession()]);
  const wallet = session?.kind === "demo" ? await walletStatus() : null;
  return <section className="page"><div className="shell">
    <header className="page-head">
      <div><p className="eyebrow">Exchange</p><h1>Markets</h1>
        <p className="lede">Experimental Zcash Shielded Assets sold by the demo dealer for native ZEC. Inventory is read from the dealer wallet&apos;s own scans; prices are fixed by the dealer.</p></div>
      {!session && demoMode() && <div className="actions"><TryDemoButton /></div>}
    </header>
    {session?.kind === "demo" && <div className="panel panel-muted wallet-strip">
      <span>{wallet?.state === "READY" ? <span className="badge badge-ok"><span className="status-dot" />Demo wallet ready</span>
        : <span className="badge badge-blue">{wallet ? "Demo wallet being prepared" : "Wallet status unavailable"}</span>}</span>
      {wallet?.state === "READY" && <span className="small"><strong>{zec(wallet.nativeZat)}</strong> test ZEC</span>}
      <span className="small muted">Network: Zcash regtest</span>
      <Link className="link-button" href={wallet?.openOrderId ? `/orders/${wallet.openOrderId}` : "/wallet"}>{wallet?.openOrderId ? "Order in progress →" : "Open wallet →"}</Link>
    </div>}
    {listing?.chainActive === false && <p className="notice notice-warn">The experimental chain is unavailable right now. Listings will return when it is back.</p>}
    {listing?.demo?.dealerPolicy === "PAUSED" && <p className="notice notice-warn">The dealer has paused new sales.</p>}
    <MarketTable market={listing} />
    <p className="small muted" style={{ marginTop: 18 }}>These are test assets on a disposable regtest chain. They are not backed by gold, government debt or credit, and they cannot be redeemed.</p>
  </div></section>;
}
