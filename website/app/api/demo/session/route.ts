import { NextRequest, NextResponse } from "next/server";
import { authCookie, clientHash, cookieOptions, currentSession, demoMode, newSid, sessionValue } from "@/lib/exchange-auth";
import { startDemo } from "@/lib/private-orders";
import { forward, remoteEnabled } from "@/lib/remote-exchange";
import { allow } from "@/lib/rate-limit";
import { clientAddress, requestOrigin } from "@/lib/request-origin";

const noStore = { "Cache-Control": "no-store" };
export const maxDuration = 30;

/** Start a judge session: the backend admits it and provisions its own wallet. */
export async function POST(request: NextRequest) {
  if (remoteEnabled()) return forward(request, "/api/demo/session", "start-demo");
  if (!demoMode()) return NextResponse.json({ error: "The hosted demo is not enabled." }, { status: 404, headers: noStore });
  if (!requestOrigin(request)) return NextResponse.json({ error: "Request rejected" }, { status: 403, headers: noStore });
  const existing = await currentSession();
  if (existing?.kind === "demo") return NextResponse.json({ next: "/wallet" }, { headers: noStore });
  const client = clientAddress(request);
  if (!allow(`demo|${client}`, 3, 1) || !allow("demo|all", 30, 30)) {
    return NextResponse.json({ error: "Too many demo sessions were started. Try again in a minute." }, { status: 429, headers: noStore });
  }
  const bucket = clientHash(client);
  if (!bucket) return NextResponse.json({ error: "The demo is not configured." }, { status: 503, headers: noStore });
  const sid = newSid();
  const result = await startDemo(sid, bucket);
  if (!result) return NextResponse.json({ error: "The demo service is unavailable." }, { status: 503, headers: noStore });
  if (!result.ok) return NextResponse.json({ error: result.error }, { status: 429, headers: noStore });
  const expiresAt = result.data.expiresAt * 1000;
  const response = NextResponse.json({ next: "/wallet" }, { headers: noStore });
  response.cookies.set(authCookie, sessionValue({ id: result.data.accountId, role: "buyer" }, "demo", sid, expiresAt),
                       cookieOptions((expiresAt - Date.now()) / 1000));
  return response;
}
