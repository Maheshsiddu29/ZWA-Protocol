import "server-only";
import type { NextRequest } from "next/server";

/** The configured public origin (hosted deployment), if any. */
export function publicOrigin(): URL | null {
  const raw = process.env.ZWA_PUBLIC_ORIGIN;
  if (!raw) return null;
  try {
    const url = new URL(raw);
    if (url.protocol !== "https:" || url.pathname !== "/" || url.search || url.hash || url.username) return null;
    return url;
  } catch { return null; }
}

/**
 * The origin a state-changing request came from, or null to reject it.
 * Hosted: Origin and Host must both equal the configured HTTPS origin.
 * Local: a loopback HTTP origin whose host equals the Host header.
 */
export function requestOrigin(request: NextRequest): string | null {
  const host = request.headers.get("host");
  const origin = request.headers.get("origin");
  if (!host || !origin) return null;
  const configured = publicOrigin();
  if (configured) return origin === configured.origin && host === configured.host ? configured.origin : null;
  if (process.env.ZWA_PUBLIC_ORIGIN) return null; // configured but invalid: fail closed
  if (!/^(?:127\.0\.0\.1|localhost):[0-9]{1,5}$/.test(host)) return null;
  try {
    const parsed = new URL(origin);
    if (parsed.protocol !== "http:" || parsed.host !== host || parsed.pathname !== "/" || parsed.search || parsed.hash) return null;
    return parsed.origin;
  } catch { return null; }
}

/**
 * The client address used only for rate limiting. Behind the deployment's
 * single reverse proxy the right-most X-Forwarded-For entry is the address
 * that proxy saw; without a trusted proxy every client shares one bucket.
 */
export function clientAddress(request: NextRequest): string {
  if (process.env.ZWA_TRUST_PROXY !== "1") return "direct";
  const hops = (request.headers.get("x-forwarded-for") || "").split(",").map(h => h.trim()).filter(Boolean);
  const last = hops.at(-1) || "";
  return /^[0-9a-fA-F:.]{2,45}$/.test(last) ? last : "unknown";
}
