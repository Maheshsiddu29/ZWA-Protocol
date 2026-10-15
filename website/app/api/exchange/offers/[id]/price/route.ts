import { NextRequest, NextResponse } from "next/server";
import { mutate } from "@/lib/exchange-route";
import { notServedHere, remoteEnabled } from "@/lib/remote-exchange";

export async function POST(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  if (remoteEnabled()) return notServedHere();
  const { id } = await context.params;
  if (!/^[0-9a-f]{32}$/.test(id)) return NextResponse.json({ error: "Offer unavailable" }, { status: 404 });
  return mutate(request, "set-price", body =>
    Number.isSafeInteger(body.priceZatPerUnit) ? { offerId: id, priceZatPerUnit: body.priceZatPerUnit } : null);
}
