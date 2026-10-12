import { NextRequest, NextResponse } from "next/server";
import { availableOffers, mutateOrder } from "@/lib/private-orders";
import { localRequestOrigin } from "@/lib/local-origin";

export const dynamic = "force-dynamic";
export async function GET() {
  const data = await availableOffers();
  if (!data) return NextResponse.json({ error: "Offers unavailable" }, { status: 403 });
  return NextResponse.json(data, { headers: { "Cache-Control": "no-store" } });
}

export async function POST(request: NextRequest) {
  if (!localRequestOrigin(request)) return NextResponse.json({ error: "Origin rejected" }, { status: 403 });
  const raw = await request.text();
  if (raw.length > 2048) return NextResponse.json({ error: "Request too large" }, { status: 413 });
  let payload: unknown;
  try { payload = JSON.parse(raw); } catch { return NextResponse.json({ error: "Invalid request" }, { status: 400 }); }
  const offer = await mutateOrder("create-offer", payload);
  if (!offer) return NextResponse.json({ error: "Offer unavailable" }, { status: 409 });
  return NextResponse.json(offer, { status: 201, headers: { "Cache-Control": "no-store" } });
}
