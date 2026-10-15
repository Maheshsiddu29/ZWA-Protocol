import { NextRequest, NextResponse } from "next/server";
import { ownOrder } from "@/lib/private-orders";
import { forward, remoteEnabled } from "@/lib/remote-exchange";

export const dynamic = "force-dynamic";
export const maxDuration = 30;
export async function GET(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  if (remoteEnabled()) return /^[0-9a-f]{32}$/.test(id) ? forward(request, `/api/exchange/orders/${id}/receipt`, "read")
    : NextResponse.json({ error: "Live receipt unavailable" }, { status: 404 });
  const order = await ownOrder(id);
  if (!order || !order.receipt) return NextResponse.json({ error: "Live receipt unavailable" }, { status: 404 });
  return NextResponse.json(order.receipt, { headers: { "Cache-Control": "no-store" } });
}
