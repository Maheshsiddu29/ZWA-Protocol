import { NextRequest, NextResponse } from "next/server";
import { authCookie } from "@/lib/exchange-auth";
import { exchangeAction, mutationAllowed } from "@/lib/private-orders";

/** End the judge session early; the disposable wallet is retired by the service. */
export async function POST(request: NextRequest) {
  if (!await mutationAllowed(request)) return NextResponse.json({ error: "Request rejected" }, { status: 403 });
  const result = await exchangeAction("demo-end");
  if (!result) return NextResponse.json({ error: "The demo service is unavailable." }, { status: 503 });
  // A failed or expired wallet has already lost its backend session. Clearing
  // its cookie lets the judge request a fresh wallet without orphaning a live one.
  if (!result.ok && result.error !== "demo session expired") {
    return NextResponse.json({ error: result.error }, { status: 409 });
  }
  const response = NextResponse.json({ next: "/" }, { headers: { "Cache-Control": "no-store" } });
  response.cookies.delete(authCookie);
  return response;
}
