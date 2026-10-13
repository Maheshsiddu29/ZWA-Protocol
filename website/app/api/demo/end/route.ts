import { NextRequest, NextResponse } from "next/server";
import { authCookie } from "@/lib/exchange-auth";
import { exchangeAction, mutationAllowed } from "@/lib/private-orders";

/** End the judge session early; the disposable wallet is retired by the service. */
export async function POST(request: NextRequest) {
  if (!await mutationAllowed(request)) return NextResponse.json({ error: "Request rejected" }, { status: 403 });
  await exchangeAction("demo-end");
  const response = NextResponse.json({ next: "/" }, { headers: { "Cache-Control": "no-store" } });
  response.cookies.delete(authCookie);
  return response;
}
