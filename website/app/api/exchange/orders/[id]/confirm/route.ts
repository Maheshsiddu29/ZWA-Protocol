import { NextRequest, NextResponse } from "next/server";
import { mutate } from "@/lib/exchange-route";

export async function POST(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  if (!/^[0-9a-f]{32}$/.test(id)) return NextResponse.json({ error: "Order unavailable" }, { status: 404 });
  // The browser can only echo the server-generated digest it was shown.
  return mutate(request, "confirm", body =>
    typeof body.quoteDigest === "string" ? { orderId: id, quoteDigest: body.quoteDigest } : null);
}
