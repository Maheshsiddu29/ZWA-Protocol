import { NextResponse } from "next/server";
import { read } from "@/lib/exchange-route";

export const dynamic = "force-dynamic";
export async function GET(_request: Request, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  if (!/^[0-9a-f]{32}$/.test(id)) return NextResponse.json({ error: "Order unavailable" }, { status: 404 });
  return read("order", { orderId: id });
}
