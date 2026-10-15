// Pure rules for the remote-exchange mode (no I/O, no Next.js imports), so they can be unit tested.
//
// Remote mode: this website renders the pages, but every session, CSRF token and exchange action
// belongs to one hosted exchange website reached over HTTPS. This deployment holds no session
// secret, portal token or role accounts; the hosted exchange verifies every request itself.

export const SESSION_COOKIE = "zwa_session";

/** Variables that must never be present next to ZWA_EXCHANGE_ORIGIN: they would let this
 *  deployment mint sessions or reach the engine directly. Their presence disables remote mode. */
export const FORBIDDEN_IN_REMOTE = ["ZWA_SESSION_SECRET", "ZWA_ROLE_ACCOUNTS", "ZWA_PORTAL_URL", "ZWA_PORTAL_TOKEN",
  "ZWA_PRIVATE_EXCHANGE_ROOT"] as const;

export type RemoteConfig =
  | { state: "off" }
  | { state: "invalid"; reason: string }
  | { state: "on"; exchange: string; site: string; siteHost: string };

/** An exact HTTPS origin (scheme, host, optional port; nothing else), or null. */
export function httpsOrigin(raw: string | undefined): URL | null {
  if (!raw || raw.length > 200) return null;
  try {
    const url = new URL(raw);
    if (url.protocol !== "https:" || url.username || url.password || url.pathname !== "/" || url.search || url.hash) return null;
    if (raw !== url.origin && raw !== `${url.origin}/`) return null;
    // One literal host name: no wildcard or other characters that URL parsing would otherwise accept.
    if (!/^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$/.test(url.hostname)) return null;
    return url;
  } catch { return null; }
}

/** Remote mode is on only with two distinct exact HTTPS origins and no backend secret in the environment. */
export function remoteConfig(env: Record<string, string | undefined>): RemoteConfig {
  if (!env.ZWA_EXCHANGE_ORIGIN) return { state: "off" };
  const exchange = httpsOrigin(env.ZWA_EXCHANGE_ORIGIN);
  if (!exchange) return { state: "invalid", reason: "ZWA_EXCHANGE_ORIGIN must be an exact https origin" };
  const site = httpsOrigin(env.ZWA_PUBLIC_ORIGIN);
  if (!site) return { state: "invalid", reason: "ZWA_PUBLIC_ORIGIN must be this site's exact https origin" };
  if (site.origin === exchange.origin) return { state: "invalid", reason: "the exchange and this site must be different origins" };
  const present = FORBIDDEN_IN_REMOTE.filter(name => env[name] !== undefined && env[name] !== "");
  if (present.length) return { state: "invalid", reason: `remove ${present.join(", ")}` };
  // The exchange's certificate must be verified: the session cookie and CSRF token travel over this link.
  if (env.NODE_TLS_REJECT_UNAUTHORIZED === "0") return { state: "invalid", reason: "remove NODE_TLS_REJECT_UNAUTHORIZED" };
  return { state: "on", exchange: exchange.origin, site: site.origin, siteHost: site.host };
}

const cookieValuePattern = /^[A-Za-z0-9_-]{16,1900}\.[0-9a-f]{64}$/;
export const validSessionCookie = (value: string | undefined | null): value is string =>
  typeof value === "string" && cookieValuePattern.test(value);

export type CookieClaim = { id: string; role: string; kind: string; sid: string; exp: number };

/** The UNVERIFIED claims inside a session cookie. Used only to skip a remote call for a cookie that is
 *  already expired or not a demo session; the exchange's own check is what grants access. */
export function cookieClaim(value: string): CookieClaim | null {
  if (!validSessionCookie(value)) return null;
  try {
    const data = JSON.parse(Buffer.from(value.split(".")[0], "base64url").toString("utf8"));
    if (!data || typeof data !== "object") return null;
    const { id, role, kind, sid, exp } = data as Record<string, unknown>;
    if (typeof id !== "string" || typeof role !== "string" || typeof kind !== "string" ||
        typeof sid !== "string" || typeof exp !== "number") return null;
    return { id, role, kind, sid, exp };
  } catch { return null; }
}

/** The React Server Components payload that Next.js inlines in a page, as one string. */
export function flightText(html: string): string {
  const parts: string[] = [];
  const push = /self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)/g;
  for (const match of html.matchAll(push)) {
    try { parts.push(JSON.parse(match[1]) as string); } catch { return ""; }
  }
  return parts.join("");
}

export const demoAccountPattern = /^judge-[0-9a-f]{20}$/;

/**
 * The demo session that the exchange's own /wallet page was rendered for: the navigation viewer
 * (root layout) and the per-session CSRF token (DemoWallet). Both must appear exactly once in a
 * consistent form, or this returns null and the visitor is treated as signed out (fail closed).
 */
export function demoSessionFromWalletPage(html: string): { id: string; csrf: string } | null {
  const text = flightText(html);
  if (!text) return null;
  const viewers = [...text.matchAll(/"viewer":\{"kind":"([a-z]+)","role":"([a-z]+)","id":"([A-Za-z0-9_-]{1,80})"\}/g)];
  const tokens = new Set([...text.matchAll(/"csrf":"([0-9a-f]{64})"/g)].map(m => m[1]));
  const anyCsrf = (text.match(/"csrf":/g) ?? []).length;
  if (viewers.length !== 1 || tokens.size !== 1 || anyCsrf !== 1) return null;
  const [, kind, role, id] = viewers[0];
  if (kind !== "demo" || role !== "buyer" || !demoAccountPattern.test(id)) return null;
  return { id, csrf: [...tokens][0] };
}

export type SessionCookieChange = { set: string; maxAge: number } | { clear: true } | null;

/** The exchange's change to the session cookie, from its Set-Cookie headers. Other cookies are ignored;
 *  a malformed or duplicated session cookie is an error ("invalid"). */
export function sessionCookieChange(headers: string[]): SessionCookieChange | "invalid" {
  const ours = headers.filter(h => h.trimStart().startsWith(`${SESSION_COOKIE}=`));
  if (ours.length === 0) return null;
  if (ours.length > 1) return "invalid";
  const [pair, ...attributes] = ours[0].split(";").map(part => part.trim());
  const value = pair.slice(SESSION_COOKIE.length + 1);
  const attr = new Map(attributes.map(a => { const i = a.indexOf("="); return i < 0 ? [a.toLowerCase(), ""] : [a.slice(0, i).toLowerCase(), a.slice(i + 1)]; }));
  const maxAge = attr.has("max-age") ? Number(attr.get("max-age")) : null;
  const expires = attr.has("expires") ? Date.parse(attr.get("expires") ?? "") : null;
  if (value === "" || maxAge === 0 || (expires !== null && expires <= Date.now() && maxAge === null)) return { clear: true };
  if (!validSessionCookie(value) || maxAge === null || !Number.isSafeInteger(maxAge) || maxAge < 1 || maxAge > 86400) return "invalid";
  return { set: value, maxAge };
}
