import type { Metadata } from "next";
import { MarketTable } from "@/components/exchange/MarketTable";
import { TryDemoButton } from "@/components/exchange/TryDemoButton";
import { currentSession, demoMode } from "@/lib/exchange-auth";
import { market } from "@/lib/private-orders";

export const metadata: Metadata = { title: "Markets" };
export const dynamic = "force-dynamic";

export default async function ExchangePage() {
  const [listing, session] = await Promise.all([market(), currentSession()]);
  return <section className="page"><div className="shell">
    <header className="page-head">
      <div><p className="eyebrow">Exchange</p><h1>Markets</h1>
        <p className="lede">Experimental Zcash Shielded Assets sold by the demo dealer for native ZEC. Inventory is read from the dealer wallet&apos;s own scans; prices are fixed by the dealer.</p></div>
      {!session && demoMode() && <div className="actions"><TryDemoButton /></div>}
    </header>
    {listing?.chainActive === false && <p className="notice notice-warn">The experimental chain is unavailable right now. Listings will return when it is back.</p>}
    {listing?.demo?.dealerPolicy === "PAUSED" && <p className="notice notice-warn">The dealer has paused new sales.</p>}
    <MarketTable market={listing} />
    <p className="small muted" style={{ marginTop: 18 }}>These are test assets on a disposable regtest chain. They are not backed by gold, government debt or credit, and they cannot be redeemed.</p>
  </div></section>;
}
