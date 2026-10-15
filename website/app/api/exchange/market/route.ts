import { NextRequest, NextResponse } from "next/server";
import { respond } from "@/lib/exchange-route";
import { exchangeAction, publicMarket } from "@/lib/private-orders";
import { forward, remoteEnabled } from "@/lib/remote-exchange";

export const dynamic = "force-dynamic";
export const maxDuration = 30;
export async function GET(request: NextRequest) {
  if (remoteEnabled()) return forward(request, "/api/exchange/market", "read");
  const own = await exchangeAction("market");
  if (own) return respond(own);
  const listing = await publicMarket();
  return listing ? NextResponse.json(listing, { headers: { "Cache-Control": "no-store" } })
    : NextResponse.json({ error: "Exchange access unavailable" }, { status: 403 });
}
