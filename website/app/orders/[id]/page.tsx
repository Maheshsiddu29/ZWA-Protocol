import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { OrderLive } from "@/components/exchange/OrderLive";
import { csrfToken, currentSession } from "@/lib/exchange-auth";
import { ownOrder } from "@/lib/private-orders";

export const metadata: Metadata = { title: "Order" };
export const dynamic = "force-dynamic";

export default async function OrderPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const session = await currentSession();
  if (!session) redirect("/exchange");
  const order = await ownOrder(id);
  if (!order) notFound();
  return <section className="page"><div className="shell">
    <p className="small"><Link href={order.viewer === "seller" ? "/seller" : "/portfolio"} className="link-button quiet">← {order.viewer === "seller" ? "Seller desk" : "Portfolio"}</Link></p>
    <OrderLive initial={order} csrf={csrfToken(session)} custodial={session.kind === "demo"} />
  </div></section>;
}
