import { NextRequest, NextResponse } from "next/server";
import { authCookie } from "@/lib/exchange-auth";
import { notServedHere, remoteEnabled } from "@/lib/remote-exchange";
import { requestOrigin } from "@/lib/request-origin";

export async function POST(request: NextRequest) {
  if (remoteEnabled()) return notServedHere();
  const origin = requestOrigin(request);
  if (!origin) return NextResponse.json({ error: "Origin rejected" }, { status: 403 });
  const response = NextResponse.redirect(new URL("/", origin), 303);
  response.cookies.delete(authCookie);
  return response;
}
