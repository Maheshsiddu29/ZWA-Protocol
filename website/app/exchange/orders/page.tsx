import Link from "next/link";
import { Protected } from "@/components/exchange/Protected";
import { boundActiveAsset } from "@/lib/exchange";

export const dynamic = "force-dynamic";
export default async function OrdersPage() {
  const active = await boundActiveAsset();
  return <Protected role="buyer" eyebrow="BUYER / ORDERS" title="My local orders." intro="Only the currently active disposable session can appear here. Historical evidence is identified separately in judge mode.">
    {active?.checkout.quote ? <div className="exchange-order"><div><span>{active.asset.name}</span><strong>{active.checkout.status || "CHAIN UNAVAILABLE"}</strong></div><div><span>Quantity</span><strong>{active.checkout.quote.quantity} units</strong></div><div><span>Exact buyer debit</span><strong>{active.checkout.quote.paymentAmount.toLocaleString("en-US")} zatoshis</strong></div><div><span>Quote</span><code>{active.checkout.quote.quoteDigest}</code></div><Link href="/demo#checkout">Inspect approval and settlement ↗</Link></div> : <div className="exchange-empty"><h2>No active local order.</h2><p>Orders are created by the operator and approved through independent wallet terminals.</p></div>}
  </Protected>;
}
