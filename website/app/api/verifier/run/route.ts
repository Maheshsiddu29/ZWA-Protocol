import { NextRequest, NextResponse } from "next/server";
import { localBackend, proxyJson } from "@/lib/backend";
export const dynamic = "force-dynamic";
const allowed = new Set(["valid-rooted-n3", "pending-a4", "checkpoint-ckn", "tampered-proof", "bad-root", "bad-terminal", "reorg"]);
export async function POST(request: NextRequest) {
  if (request.headers.get("content-type")?.split(";")[0] !== "application/json") return NextResponse.json({ error: { code: "CONTENT_TYPE_REQUIRED" } }, { status: 415 });
  const body = await request.json().catch(() => null);
  if (!body || typeof body !== "object" || Object.keys(body).length !== 1 || !allowed.has(body.scenarioId)) return NextResponse.json({ error: { code: "INVALID_SCENARIO" } }, { status: 400 });
  return proxyJson(localBackend(process.env.ZWA_VERIFIER_URL, 3000), "/v1/demo/run", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
}
