import "server-only";
import { createHash, createHmac, randomBytes, scryptSync, timingSafeEqual } from "node:crypto";
import { cookies } from "next/headers";

export type Role = "buyer" | "seller" | "issuer" | "auditor";
export const roles: Role[] = ["buyer", "seller", "issuer", "auditor"];
type Account = { id: string; role: Role; salt: string; hash: string };
const cookieName = "zwa_demo_session";

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

export function authenticate(id: string, password: string): { id: string; role: Role } | null {
  const config = configuration();
  if (!config || !id || !password || id.length > 80 || password.length > 512) return null;
  const account = config.accounts.find((entry) => entry.id === id && roles.includes(entry.role));
  if (!account || !/^[0-9a-f]{32}$/.test(account.salt) || !/^[0-9a-f]{128}$/.test(account.hash)) return null;
  const derived = scryptSync(password, Buffer.from(account.salt, "hex"), 64);
  if (!timingSafeEqual(derived, Buffer.from(account.hash, "hex"))) return null;
  return { id: account.id, role: account.role };
}

export function sessionValue(account: { id: string; role: Role }): string {
  const config = configuration();
  if (!config) throw new Error("Role authentication is not provisioned");
  // The random session ID binds wallet pairings and CSRF tokens to this login.
  const sid = randomBytes(16).toString("hex");
  const payload = Buffer.from(JSON.stringify({ id: account.id, role: account.role, sid, exp: Date.now() + 4 * 60 * 60 * 1000 })).toString("base64url");
  return `${payload}.${mac(payload, config.secret)}`;
}

export type Session = { id: string; role: Role; sid: string };

export async function currentSession(): Promise<Session | null> {
  const value = (await cookies()).get(cookieName)?.value;
  const config = configuration();
  if (!value || value.length > 2048 || !config) return null;
  const [payload, tag, extra] = value.split(".");
  if (!payload || !tag || extra || !/^[0-9a-f]{64}$/.test(tag)) return null;
  const expected = mac(payload, config.secret);
  if (!timingSafeEqual(Buffer.from(tag, "hex"), Buffer.from(expected, "hex"))) return null;
  try {
    const data = JSON.parse(Buffer.from(payload, "base64url").toString()) as { id: string; role: Role; sid: string; exp: number };
    if (typeof data.exp !== "number" || data.exp <= Date.now() || !roles.includes(data.role)) return null;
    if (typeof data.sid !== "string" || !/^[0-9a-f]{32}$/.test(data.sid)) return null;
    if (!config.accounts.some((entry) => entry.id === data.id && entry.role === data.role)) return null;
    return { id: data.id, role: data.role, sid: data.sid };
  } catch { return null; }
}

export async function currentAccount(): Promise<{ id: string; role: Role } | null> {
  const session = await currentSession();
  return session ? { id: session.id, role: session.role } : null;
}

/** Stable, non-reversible session identifier given to the exchange bridge. */
export function sessionHash(session: Session): string {
  return createHash("sha256").update(`ZWA4E_PORTAL_SESSION_V1|${session.sid}`).digest("hex");
}

export function csrfToken(session: Session): string {
  const config = configuration();
  if (!config) throw new Error("Role authentication is not provisioned");
  return mac(`csrf|${session.id}|${session.role}|${session.sid}`, config.secret);
}

export async function csrfValid(header: string | null): Promise<boolean> {
  const session = await currentSession();
  if (!session || !header || !/^[0-9a-f]{64}$/.test(header)) return false;
  return timingSafeEqual(Buffer.from(header, "hex"), Buffer.from(csrfToken(session), "hex"));
}

export async function hasRole(role: Role): Promise<boolean> {
  return (await currentAccount())?.role === role;
}

export const authCookie = cookieName;
