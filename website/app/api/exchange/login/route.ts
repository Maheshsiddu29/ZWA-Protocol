import { NextRequest, NextResponse } from "next/server";
import { authenticate, authCookie, sessionValue } from "@/lib/exchange-auth";
import { localRequestOrigin } from "@/lib/local-origin";

export async function POST(request: NextRequest) {
  const origin = localRequestOrigin(request);
  if (!origin) return NextResponse.json({ error: "Origin rejected" }, { status: 403 });
  if (Number(request.headers.get("content-length") || 0) > 4096) return NextResponse.json({ error: "Request too large" }, { status: 413 });
  const form = await request.formData();
  const id = form.get("id"), password = form.get("password");
  const account = typeof id === "string" && typeof password === "string" ? authenticate(id, password) : null;
  if (!account) return NextResponse.redirect(new URL("/exchange/connect?error=credentials", origin), 303);
  const path = account.role === "buyer" ? "/exchange/portfolio" : `/${account.role}`;
  const response = NextResponse.redirect(new URL(path, origin), 303);
  response.cookies.set(authCookie, sessionValue(account), { httpOnly: true, sameSite: "strict", secure: false, path: "/", maxAge: 4 * 60 * 60 });
  return response;
}
