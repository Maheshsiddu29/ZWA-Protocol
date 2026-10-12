import { NextResponse } from "next/server";
import { localBackend, proxyJson } from "@/lib/backend";
export const dynamic = "force-dynamic";
const paths: Record<string, string> = {
  info: "/v1/checkout/info",
  quote: "/v1/checkout/sample-quote",
  session: "/v1/checkout/sessions/recorded-phase35h",
  receipt: "/v1/checkout/sessions/recorded-phase35h/receipt",
  active: "/v1/checkout/active",
};
export async function GET(_request: Request, { params }: { params: Promise<{ kind: string }> }) {
  const { kind } = await params;
  if (!Object.hasOwn(paths, kind)) return NextResponse.json({ error: { code: "NOT_FOUND" } }, { status: 404 });
  return proxyJson(localBackend(process.env.ZWA_CHECKOUT_URL, 18745), paths[kind]);
}
