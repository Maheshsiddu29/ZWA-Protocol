import { notFound } from "next/navigation";
import { Participant } from "@/components/exchange/Participant";
import { OrderLive } from "@/components/exchange/OrderLive";
import { ownOrder } from "@/lib/private-orders";

export const dynamic = "force-dynamic";
export default async function OrderPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const order = await ownOrder(id);
  if (!order) notFound();
  return <Participant eyebrow="ORDER" title="Order status." intro="Every step below comes from the private ledger, the proof verifier, your wallet, or the experimental chain.">
    {(_session, csrf) => <OrderLive initial={order} csrf={csrf} />}
  </Participant>;
}
