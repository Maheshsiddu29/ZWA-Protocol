import { NextResponse } from "next/server";
import { ownOrder } from "@/lib/private-orders";

export const dynamic = "force-dynamic";
export async function GET(_request: Request, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  const order = await ownOrder(id);
  if (!order) return NextResponse.json({ error: "Order unavailable" }, { status: 404 });
  return NextResponse.json(order, { headers: { "Cache-Control": "no-store" } });
}
