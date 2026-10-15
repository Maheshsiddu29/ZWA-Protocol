import "server-only";
import { createHash, createHmac, randomBytes, scryptSync, timingSafeEqual } from "node:crypto";
import { cookies } from "next/headers";
import { remote, remoteSession } from "@/lib/remote-exchange";

export type Role = "buyer" | "seller";
export const roles: Role[] = ["buyer", "seller"];
type Account = { id: string; role: Role; salt: string; hash: string };
/** "account": an operator-provisioned password login. "demo": a judge session
 *  bound server-side to one disposable custodial demo wallet. */
export type SessionKind = "account" | "demo";
const cookieName = "zwa_session";
const accountLifetimeMs = 4 * 60 * 60 * 1000;
export const demoAccount = /^judge-[0-9a-f]{20}$/;

/** Remote mode (ZWA_EXCHANGE_ORIGIN) serves only the judge demo, from the hosted exchange. */
export const demoMode = () => process.env.ZWA_DEMO_MODE === "1" || remote().state === "on";

function configuration(): { accounts: Account[]; secret: Buffer } | null {
  try {
    const secret = Buffer.from(process.env.ZWA_SESSION_SECRET || "", "hex");
    const accounts = JSON.parse(process.env.ZWA_ROLE_ACCOUNTS || "[]") as Account[];
    if (secret.length !== 32 || !Array.isArray(accounts)) return null;
    return { accounts, secret };
  } catch { return null; }
}

function mac(payload: string, secret: Buffer): string {
  return createHmac("sha256", secret).update(payload).digest("hex");
}

/** Non-reversible rate-limit bucket for a client address. */
export function clientHash(client: string): string | null {
  const config = configuration();
  return config ? mac(`client|${client}`, config.secret) : null;
}

export function authenticate(id: string, password: string): { id: string; role: Role } | null {
  const config = configuration();
  if (!config || !id || !password || id.length > 80 || password.length > 512) return null;
  const account = config.accounts.find((entry) => entry.id === id && roles.includes(entry.role));
  if (!account || !/^[0-9a-f]{32}$/.test(account.salt) || !/^[0-9a-f]{128}$/.test(account.hash)) return null;
  const derived = scryptSync(password, Buffer.from(account.salt, "hex"), 64);
  if (!timingSafeEqual(derived, Buffer.from(account.hash, "hex"))) return null;
  return { id: account.id, role: account.role };
}

/** A fresh random session ID; its hash binds wallet records and CSRF tokens. */
export const newSid = () => randomBytes(16).toString("hex");

export function sessionValue(account: { id: string; role: Role }, kind: SessionKind = "account",
                             sid: string = newSid(), expiresAt?: number): string {
  const config = configuration();
  if (!config) throw new Error("Session signing is not provisioned");
  const exp = expiresAt ?? Date.now() + accountLifetimeMs;
  const payload = Buffer.from(JSON.stringify({ id: account.id, role: account.role, kind, sid, exp })).toString("base64url");
  return `${payload}.${mac(payload, config.secret)}`;
}

/** `csrf` is set only in remote mode, where the hosted exchange issued the token. */
export type Session = { id: string; role: Role; sid: string; kind: SessionKind; exp: number; csrf?: string };

export async function currentSession(): Promise<Session | null> {
  // Remote mode: the hosted exchange verifies the cookie; this deployment holds no secret.
  if (remote().state !== "off") return remoteSession();
  const value = (await cookies()).get(cookieName)?.value;
  const config = configuration();
  if (!value || value.length > 2048 || !config) return null;
  const [payload, tag, extra] = value.split(".");
  if (!payload || !tag || extra || !/^[0-9a-f]{64}$/.test(tag)) return null;
  const expected = mac(payload, config.secret);
  if (!timingSafeEqual(Buffer.from(tag, "hex"), Buffer.from(expected, "hex"))) return null;
  try {
    const data = JSON.parse(Buffer.from(payload, "base64url").toString()) as Session;
    if (typeof data.exp !== "number" || data.exp <= Date.now() || !roles.includes(data.role)) return null;
    if (typeof data.sid !== "string" || !/^[0-9a-f]{32}$/.test(data.sid)) return null;
    if (data.kind === "demo") {
      // The backend re-checks every demo request against its live wallet record.
      if (!demoMode() || data.role !== "buyer" || !demoAccount.test(data.id)) return null;
    } else if (data.kind !== "account" || !config.accounts.some((entry) => entry.id === data.id && entry.role === data.role)) {
      return null;
    }
    return { id: data.id, role: data.role, sid: data.sid, kind: data.kind, exp: data.exp };
  } catch { return null; }
}

/** Stable, non-reversible session identifier given to the exchange bridge. */
export function sessionHash(session: { sid: string }): string {
  return createHash("sha256").update(`ZWA4E_PORTAL_SESSION_V1|${session.sid}`).digest("hex");
}

export function csrfToken(session: Session): string {
  if (remote().state !== "off") return session.csrf ?? "";
  const config = configuration();
  if (!config) throw new Error("Session signing is not provisioned");
  return mac(`csrf|${session.id}|${session.role}|${session.sid}`, config.secret);
}

export async function csrfValid(header: string | null): Promise<boolean> {
  const session = await currentSession();
  if (!session || !header || !/^[0-9a-f]{64}$/.test(header)) return false;
  return timingSafeEqual(Buffer.from(header, "hex"), Buffer.from(csrfToken(session), "hex"));
}

/** Cookie attributes: Secure whenever the site is served over HTTPS. */
export function cookieOptions(maxAgeSeconds: number) {
  const secure = (process.env.ZWA_PUBLIC_ORIGIN || "").startsWith("https://");
  return { httpOnly: true, sameSite: "strict" as const, secure, path: "/", maxAge: Math.max(0, Math.floor(maxAgeSeconds)) };
}

export const authCookie = cookieName;
