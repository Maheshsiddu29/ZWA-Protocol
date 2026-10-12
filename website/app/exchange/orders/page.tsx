import Link from "next/link";
import { Protected } from "@/components/exchange/Protected";
import { OrderRequest } from "@/components/exchange/OrderRequest";
import { availableOffers, ownOrders } from "@/lib/private-orders";

export const dynamic = "force-dynamic";
export default async function OrdersPage() {
  const [orders, offers] = await Promise.all([ownOrders(), availableOffers()]);
  return <Protected role="buyer" eyebrow="BUYER / ORDERS" title="My local orders." intro="The order timeline comes from the private ledger. A receipt appears only while the accepted transaction remains in the live experimental chain.">
    {offers && <OrderRequest offers={offers.offers ?? []} />}
    {orders?.length ? orders.map(order => <div className="exchange-order" key={order.id}>
      <div><span>Instrument</span><strong>{order.instrumentId}</strong></div>
      <div><span>State</span><strong>{order.state.replaceAll("_", " ")}</strong></div>
      <div><span>Quantity</span><strong>{order.quantity} unit{order.quantity === 1 ? "" : "s"}</strong></div>
      {order.quote && <div><span>Exact native ZEC payment</span><strong>{order.quote.paymentZat.toLocaleString("en-US")} zatoshis</strong></div>}
      {order.quote && <div><span>Quote digest</span><code>{order.quote.digest}</code></div>}
      <div><span>Proof</span><strong>{order.proofVerified ? "Groth16 verified" : "Not verified"}</strong></div>
      <div><span>Buyer approval</span><strong>{order.buyerApprovalMode?.replaceAll("_", " ") ?? "Pending"}</strong></div>
      <div><span>Seller approval</span><strong>{order.sellerApprovalMode?.replaceAll("_", " ") ?? "Pending"}</strong></div>
      <div><span>Timeline</span><strong>{order.events.map(event => event.to_state.replaceAll("_", " ")).join(" → ")}</strong></div>
      {order.receipt ? <div><span>Live receipt</span><strong>Tx {order.receipt.txid} · height {order.receipt.height} · {order.receipt.approvalMode.replaceAll("_", " ")}</strong></div> : <div><span>Live chain</span><strong>{order.chainActive ? "No receipt yet" : "Unavailable"}</strong></div>}
      <Link href="/exchange/portfolio">View scanned holdings ↗</Link>
    </div>) : <div className="exchange-empty"><h2>No private order to display.</h2><p>Sign in with the buyer identity provisioned for this local wallet.</p></div>}
    <p className="exchange-caveat">Scripted test signer approvals are separate from human wallet consent. The portal does not hold wallet seeds or release signatures.</p>
  </Protected>;
}
