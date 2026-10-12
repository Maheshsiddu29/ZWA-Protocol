import "server-only";
import type { NextRequest } from "next/server";

export function localRequestOrigin(request: NextRequest): string | null {
  const host = request.headers.get("host");
  const origin = request.headers.get("origin");
  if (!host || !/^(?:127\.0\.0\.1|localhost):[0-9]{1,5}$/.test(host) || !origin) return null;
  try {
    const parsed = new URL(origin);
    if (parsed.protocol !== "http:" || parsed.host !== host || parsed.pathname !== "/" || parsed.search || parsed.hash) return null;
    return parsed.origin;
  } catch { return null; }
}
