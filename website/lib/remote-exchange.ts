import "server-only";
import { cache } from "react";
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { allow } from "@/lib/rate-limit";
import { clientAddress, requestOrigin } from "@/lib/request-origin";
import { SESSION_COOKIE, cookieClaim, demoSessionFromWalletPage, remoteConfig, sessionCookieChange,
         validSessionCookie, type RemoteConfig } from "@/lib/remote-contract";

/** Remote mode (ZWA_EXCHANGE_ORIGIN set): "on", or "invalid" (everything exchange-related fails closed). */
export const remote = (): RemoteConfig => remoteConfig(process.env);
export const remoteEnabled = () => remote().state !== "off";
const exchangeOrigin = () => { const config = remote(); return config.state === "on" ? config.exchange : null; };

const noStore = { "Cache-Control": "no-store" };
const maxJson = 262144;
const maxPage = 1048576;
const unknownOutcome = { error: "The exchange did not confirm the result. It may or may not have been applied.", outcome: "unknown" };

type Answer = { status: number; type: string; text: string; setCookie: string[] };

/** One request to the hosted exchange website: no redirects, no caching, bounded size and time. */
async function call(path: string, init: { method: "GET" | "POST"; cookie?: string; csrf?: string; body?: string; accept: string },
                    timeoutMs: number, limit: number): Promise<Answer | null> {
  const origin = exchangeOrigin();
  if (!origin || !path.startsWith("/")) return null;
  const headers: Record<string, string> = { accept: init.accept };
  if (init.cookie) headers.cookie = `${SESSION_COOKIE}=${init.cookie}`;
  if (init.method === "POST") {
    // The exchange accepts state changes only from its own origin; this server has already
    // enforced this site's exact origin, and the exchange still checks the session's CSRF token.
    headers.origin = origin;
    headers["content-type"] = "application/json";
    if (init.csrf) headers["x-zwa-csrf"] = init.csrf;
  }
  try {
    const response = await fetch(`${origin}${path}`, { method: init.method, headers, body: init.body, redirect: "manual",
      cache: "no-store", signal: AbortSignal.timeout(timeoutMs) });
    const text = await response.text();
    if (text.length > limit) return null;
    return { status: response.status, type: response.headers.get("content-type") ?? "", text,
             setCookie: response.headers.getSetCookie() };
  } catch { return null; }
}

function json(answer: Answer | null): unknown {
  if (!answer || !answer.type.startsWith("application/json")) return undefined;
  try { return JSON.parse(answer.text); } catch { return undefined; }
}

const sessionCookie = async () => {
  const value = (await cookies()).get(SESSION_COOKIE)?.value;
  return validSessionCookie(value) ? value : null;
};

export type RemoteSession = { id: string; role: "buyer"; kind: "demo"; sid: string; exp: number; csrf: string };

/**
 * The visitor's demo session as the exchange sees it, once per render. The exchange's own /wallet
 * page is rendered for this cookie; it verifies the signature, expiry and live wallet binding, and
 * it is the only source of the session's CSRF token. Operator accounts are not served here.
 */
export const remoteSession = cache(async (): Promise<RemoteSession | null> => {
  const cookie = await sessionCookie();
  const claim = cookie ? cookieClaim(cookie) : null;
  if (!cookie || !claim || claim.kind !== "demo" || claim.exp <= Date.now() || !/^[0-9a-f]{32}$/.test(claim.sid)) return null;
  const page = await call("/wallet", { method: "GET", cookie, accept: "text/html" }, 8000, maxPage);
  if (!page || page.status !== 200 || !page.type.startsWith("text/html")) return null;
  const seen = demoSessionFromWalletPage(page.text);
  if (!seen || seen.id !== claim.id) return null;
  return { id: seen.id, role: "buyer", kind: "demo", sid: claim.sid, exp: claim.exp, csrf: seen.csrf };
});

type Bridge<T> = { ok: true; data: T } | { ok: false; error: string; unknown?: true };

const readPaths: Record<string, (payload: Record<string, unknown>) => string | null> = {
  market: () => "/api/exchange/market",
  wallet: () => "/api/exchange/wallet",
  orders: () => "/api/exchange/orders",
  order: payload => typeof payload.orderId === "string" && /^[0-9a-f]{32}$/.test(payload.orderId) ? `/api/exchange/orders/${payload.orderId}` : null,
};

/** A read action through the exchange's JSON API, as the visitor (null: no access or no answer). */
export async function remoteRead<T>(action: string, payload: Record<string, unknown> = {}): Promise<Bridge<T> | null> {
  const path = readPaths[action]?.(payload);
  const cookie = await sessionCookie();
  if (!path || !cookie) return null;
  const answer = await call(path, { method: "GET", cookie, accept: "application/json" }, 8000, maxJson);
  const body = json(answer) as { error?: unknown; outcome?: unknown } | undefined;
  if (!answer || body === undefined || body === null || typeof body !== "object") return null;
  if (answer.status === 200) return { ok: true, data: body as T };
  const error = typeof body.error === "string" ? body.error.slice(0, 200) : "The exchange refused the request.";
  if (answer.status === 503 && body.outcome === "unknown") return { ok: false, error, unknown: true };
  if (answer.status === 409) return { ok: false, error };
  return null;
}

/** The dealer's public listings (no session). Short timeout: the home page renders without them. */
export async function remotePublicMarket<T>(): Promise<T | null> {
  const answer = await call("/api/exchange/market", { method: "GET", accept: "application/json" }, 5000, maxJson);
  const body = json(answer);
  return answer?.status === 200 && body && typeof body === "object" ? body as T : null;
}

export type Forward = "read" | "mutation" | "start-demo" | "end-demo";
const passThrough = new Set([200, 400, 403, 404, 409, 413, 429, 503]);

/**
 * Proxy one fixed exchange API route. State changes must come from this site's exact origin and
 * carry a well-formed CSRF token, which the exchange verifies against the session. Only the session
 * cookie, the CSRF header and a re-serialized small JSON body are sent; only JSON, the status and the
 * session cookie (from start and end only) come back. Any unclear answer to a state change is
 * reported as "unknown outcome", never as a refusal or success.
 */
export async function forward(request: NextRequest, path: string, kind: Forward): Promise<NextResponse> {
  const write = kind !== "read";
  if (!exchangeOrigin()) return NextResponse.json({ error: "Exchange access unavailable" }, { status: 503, headers: noStore });
  const cookie = request.cookies.get(SESSION_COOKIE)?.value;
  const session = validSessionCookie(cookie) ? cookie : undefined;
  let body: string | undefined;
  let csrf: string | undefined;
  if (write) {
    if (!requestOrigin(request)) return NextResponse.json({ error: "Request rejected" }, { status: 403, headers: noStore });
    if (Number(request.headers.get("content-length") || 0) > 2048) return NextResponse.json({ error: "Request too large" }, { status: 413, headers: noStore });
    const raw = await request.text();
    let parsed: unknown;
    try { parsed = JSON.parse(raw || "{}"); } catch { parsed = null; }
    if (raw.length > 2048 || !parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return NextResponse.json({ error: "Invalid request" }, { status: 400, headers: noStore });
    }
    body = JSON.stringify(parsed);
    if (kind === "start-demo") {
      if (!allow(`demo|${clientAddress(request)}`, 3, 1) || !allow("demo|all", 30, 30)) {
        return NextResponse.json({ error: "Too many demo sessions were started. Try again in a minute." }, { status: 429, headers: noStore });
      }
    } else {
      const header = request.headers.get("x-zwa-csrf") ?? "";
      if (!session || !/^[0-9a-f]{64}$/.test(header)) return NextResponse.json({ error: "Request rejected" }, { status: 403, headers: noStore });
      csrf = header;
    }
  }
  const answer = await call(path, { method: write ? "POST" : "GET", cookie: session, csrf, body, accept: "application/json" },
                            write ? 28000 : 15000, maxJson);
  const data = json(answer);
  if (!answer || data === undefined || !passThrough.has(answer.status)) {
    return write ? NextResponse.json(kind === "start-demo" ? { error: "The demo service is unavailable." } : unknownOutcome, { status: 503, headers: noStore })
      : NextResponse.json({ error: "Exchange unavailable" }, { status: 502, headers: noStore });
  }
  const response = NextResponse.json(data, { status: answer.status, headers: noStore });
  const change = sessionCookieChange(answer.setCookie);
  if (change === "invalid") return NextResponse.json({ error: "The demo service is unavailable." }, { status: 503, headers: noStore });
  const options = { httpOnly: true, sameSite: "strict" as const, secure: true, path: "/" };
  if (kind === "start-demo" && answer.status === 200 && change && "set" in change) {
    response.cookies.set(SESSION_COOKIE, change.set, { ...options, maxAge: change.maxAge });
  } else if (kind === "end-demo" && answer.status === 200 && change && "clear" in change) {
    response.cookies.set(SESSION_COOKIE, "", { ...options, maxAge: 0 });
  }
  return response;
}

/** Operator-only routes are not served by a remote-mode deployment. */
export const notServedHere = () =>
  NextResponse.json({ error: "Not available on this site." }, { status: 404, headers: noStore });
