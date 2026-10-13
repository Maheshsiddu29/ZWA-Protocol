import { NextResponse } from "next/server";
import { respond } from "@/lib/exchange-route";
import { exchangeAction, publicMarket } from "@/lib/private-orders";

export const dynamic = "force-dynamic";
export async function GET() {
  const own = await exchangeAction("market");
  if (own) return respond(own);
  const listing = await publicMarket();
  return listing ? NextResponse.json(listing, { headers: { "Cache-Control": "no-store" } })
    : NextResponse.json({ error: "Exchange access unavailable" }, { status: 403 });
}
