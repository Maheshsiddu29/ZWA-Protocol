import { Protected } from "@/components/exchange/Protected";
import { ownOrders } from "@/lib/private-orders";
import { participantPortfolio } from "@/lib/persistent-portfolio";

export const dynamic = "force-dynamic";
export default async function ActivityPage() {
  const [orders, portfolio] = await Promise.all([ownOrders(), participantPortfolio("buyer")]);
  const settled = orders?.filter(order => order.receipt) ?? [];
  return <Protected role="buyer" eyebrow="BUYER / ACTIVITY" title="Transaction activity." intro="Each receipt is checked against the live chain and current wallet holdings.">
    {portfolio && settled.length ? settled.map(order => <div className="exchange-order" key={order.id}>
      <div><span>Instrument</span><strong>{order.instrumentId}</strong></div>
      <div><span>Transaction</span><code>{order.receipt?.txid}</code></div>
      <div><span>Height</span><strong>{order.receipt?.height}</strong></div>
      <div><span>Current chain tip</span><strong>{order.chainHeight}</strong></div>
      <div><span>Wallet effect</span><strong>{order.quantity} unit acquired · {order.receipt?.paymentZat.toLocaleString("en-US")} zatoshis paid</strong></div>
      <div><span>Approval evidence</span><strong>{order.receipt?.approvalMode.replaceAll("_", " ")}</strong></div>
    </div>) : <div className="exchange-empty"><h2>No current-chain order receipt.</h2><p>The ledger may retain history while the node or wallet scan is unavailable; this page shows live verified receipts only.</p></div>}
  </Protected>;
}
