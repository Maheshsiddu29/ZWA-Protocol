import { NextRequest, NextResponse } from "next/server";
import { authCookie } from "@/lib/exchange-auth";
import { localRequestOrigin } from "@/lib/local-origin";

export async function POST(request: NextRequest) {
  const origin = localRequestOrigin(request);
  if (!origin) return NextResponse.json({ error: "Origin rejected" }, { status: 403 });
  const response = NextResponse.redirect(new URL("/exchange", origin), 303);
  response.cookies.delete(authCookie);
  return response;
}
