import Link from "next/link";
import { assetName, zec } from "@/lib/exchange";
import { stateLabel, verdict } from "@/lib/order-labels";
import type { PrivateOrder } from "@/lib/private-orders";

export function OrderList({ orders, empty }: { orders: PrivateOrder[]; empty: string }) {
  if (!orders.length) return <p className="muted small">{empty}</p>;
  return <div className="list">{orders.map(order => {
    const result = verdict(order);
    const label = result === "VERIFIED" ? "Settlement verified" : result === "CONFIRMING" ? "Confirming durability"
      : result === "NOT_VISIBLE" ? "Not visible on chain" : stateLabel[order.state] ?? order.state;
    return <Link className="list-row" key={order.id} href={`/orders/${order.id}`}>
      <span><strong>{order.viewer === "buyer" ? "Bought" : "Sold"} {order.quantity} {assetName(order.instrumentId)}</strong>
        <small>{new Date(order.events[0].occurred_at * 1000).toLocaleString()}{order.viewer === "seller" ? ` · ${order.buyerId}` : ""}</small></span>
      <span className="num hide-sm">{order.quote ? zec(order.quote.paymentZat) : "—"}</span>
      <span className="hide-sm">{order.receipt ? <small className="hash">tx {order.receipt.txid.slice(0, 12)}…</small> : null}</span>
      <span className={`badge ${result === "VERIFIED" ? "badge-ok" : result === "NOT_SETTLED" ? "badge-bad" : result === "UNCERTAIN" || result === "NOT_VISIBLE" ? "badge-warn" : "badge-blue"}`}>{label}</span>
    </Link>;
  })}</div>;
}
