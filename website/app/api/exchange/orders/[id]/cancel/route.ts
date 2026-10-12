import { NextRequest, NextResponse } from "next/server";
import { mutateOrder } from "@/lib/private-orders";
import { localRequestOrigin } from "@/lib/local-origin";

export async function POST(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  if (!localRequestOrigin(request)) return NextResponse.json({ error: "Origin rejected" }, { status: 403 });
  const { id } = await context.params;
  if (!/^[0-9a-f]{32}$/.test(id)) return NextResponse.json({ error: "Order unavailable" }, { status: 404 });
  const order = await mutateOrder("cancel", { orderId: id });
  if (!order) return NextResponse.json({ error: "Cancellation unavailable" }, { status: 409 });
  return NextResponse.json(order, { headers: { "Cache-Control": "no-store" } });
}
