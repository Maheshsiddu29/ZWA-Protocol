import { NextResponse } from "next/server";
import { ownOrder } from "@/lib/private-orders";

export const dynamic = "force-dynamic";
export async function GET(_request: Request, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  const order = await ownOrder(id);
  if (!order || !order.receipt) return NextResponse.json({ error: "Live receipt unavailable" }, { status: 404 });
  return NextResponse.json(order.receipt, { headers: { "Cache-Control": "no-store" } });
}
