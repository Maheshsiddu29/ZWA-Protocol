import { NextRequest, NextResponse } from "next/server";
import { authCookie, authenticate, cookieOptions, sessionValue } from "@/lib/exchange-auth";
import { allow } from "@/lib/rate-limit";
import { clientAddress, requestOrigin } from "@/lib/request-origin";

export async function POST(request: NextRequest) {
  const origin = requestOrigin(request);
  if (!origin) return NextResponse.json({ error: "Origin rejected" }, { status: 403 });
  if (Number(request.headers.get("content-length") || 0) > 4096) return NextResponse.json({ error: "Request too large" }, { status: 413 });
  if (!allow(`login|${clientAddress(request)}`, 5, 5)) return NextResponse.redirect(new URL("/sign-in?error=rate", origin), 303);
  const form = await request.formData();
  const id = form.get("id"), password = form.get("password");
  const account = typeof id === "string" && typeof password === "string" ? authenticate(id, password) : null;
  if (!account) return NextResponse.redirect(new URL("/sign-in?error=credentials", origin), 303);
  const response = NextResponse.redirect(new URL(account.role === "seller" ? "/seller" : "/portfolio", origin), 303);
  response.cookies.set(authCookie, sessionValue(account), cookieOptions(4 * 60 * 60));
  return response;
}
