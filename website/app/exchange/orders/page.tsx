import Link from "next/link";
import { Participant } from "@/components/exchange/Participant";
import { stateLabel, zat } from "@/lib/order-labels";
import { market, ownOrders } from "@/lib/private-orders";

export const dynamic = "force-dynamic";
export default async function OrdersPage() {
  const [orders, offers] = await Promise.all([ownOrders(), market()]);
  return <Participant eyebrow="ORDERS" title="My orders." intro="Orders come from the private ledger. A receipt appears only while its transaction remains in the live experimental chain.">
    {(session) => <>
      {session.role === "buyer" && offers?.instruments.length ? <section className="exchange-order"><div><span>Available now</span><strong>{offers.instruments.filter(o => o.availableUnits > 0 && o.priceZatPerUnit).map(o => <Link key={o.offerId} href={`/exchange/assets/${o.instrumentId}`}>{o.name} ↗ </Link>)}</strong></div></section> : null}
      {orders?.length ? orders.map(order => <Link className="exchange-order-row" key={order.id} href={`/exchange/orders/${order.id}`}>
        <div><span className="exchange-eyebrow">{order.instrumentId}</span><strong>{stateLabel[order.state] ?? order.state}</strong></div>
        <div><span>{order.quantity} unit{order.quantity === 1 ? "" : "s"}</span><strong>{order.quote ? zat(order.quote.paymentZat) : "Quote pending"}</strong></div>
        <div><span>{order.receipt ? `Tx ${order.receipt.txid.slice(0, 12)}…` : new Date(order.events[0].occurred_at * 1000).toLocaleString()}</span><strong>Open ↗</strong></div>
      </Link>) : <div className="exchange-empty"><h2>No orders yet.</h2><p>Browse assets to start a purchase.</p><Link href="/exchange/assets">Browse assets ↗</Link></div>}
    </>}
  </Participant>;
}
