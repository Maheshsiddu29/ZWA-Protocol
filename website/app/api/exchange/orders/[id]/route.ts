import { NextRequest, NextResponse } from "next/server";
import { read } from "@/lib/exchange-route";
import { forward, remoteEnabled } from "@/lib/remote-exchange";

export const dynamic = "force-dynamic";
export const maxDuration = 30;
export async function GET(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  if (!/^[0-9a-f]{32}$/.test(id)) return NextResponse.json({ error: "Order unavailable" }, { status: 404 });
  if (remoteEnabled()) return forward(request, `/api/exchange/orders/${id}`, "read");
  return read("order", { orderId: id });
}
