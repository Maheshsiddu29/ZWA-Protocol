import { NextRequest, NextResponse } from "next/server";
import { localBackend, proxyJson } from "@/lib/backend";
export const dynamic = "force-dynamic";
export async function POST(request: NextRequest) {
  if (request.headers.get("content-type")?.split(";")[0] !== "application/json") return NextResponse.json({ error: { code: "CONTENT_TYPE_REQUIRED" } }, { status: 415 });
  if (Number(request.headers.get("content-length") || 0) > 7_200_000) return NextResponse.json({ error: { code: "REQUEST_TOO_LARGE" } }, { status: 413 });
  const body = await request.json().catch(() => null);
  if (!body || typeof body !== "object" || Object.keys(body).sort().join(",") !== "evidence,evidenceEncoding" || body.evidenceEncoding !== "base64" || typeof body.evidence !== "string" || body.evidence.length > 7_130_000 || !/^[A-Za-z0-9+/]+={0,2}$/.test(body.evidence)) return NextResponse.json({ error: { code: "INVALID_EVIDENCE_ENVELOPE" } }, { status: 400 });
  return proxyJson(localBackend(process.env.ZWA_VERIFIER_URL, 3000), "/v1/lineage/verify", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
}
