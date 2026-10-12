import { NextResponse } from "next/server";

export function localBackend(env: string | undefined, fallbackPort: number): string {
  const url = new URL(env || `http://127.0.0.1:${fallbackPort}`);
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(url.hostname) || url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
    throw new Error("Backend URL must be a loopback HTTP origin");
  }
  return url.origin;
}

export async function proxyJson(origin: string, path: string, init?: RequestInit) {
  try {
    const response = await fetch(`${origin}${path}`, { ...init, cache: "no-store", signal: AbortSignal.timeout(init?.method === "POST" ? 60000 : 6000) });
    const body = await response.text();
    if (body.length > 1_000_000) return NextResponse.json({ error: { code: "UPSTREAM_RESPONSE_TOO_LARGE" } }, { status: 502 });
    const data = JSON.parse(body);
    return NextResponse.json(data, { status: response.status, headers: { "Cache-Control": "no-store" } });
  } catch {
    return NextResponse.json({ error: { code: "BACKEND_UNAVAILABLE", message: "Local research backend is unavailable." } }, { status: 503, headers: { "Cache-Control": "no-store" } });
  }
}
