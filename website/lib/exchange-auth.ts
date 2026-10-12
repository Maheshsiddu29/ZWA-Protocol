import "server-only";
import { createHmac, scryptSync, timingSafeEqual } from "node:crypto";
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
  const payload = Buffer.from(JSON.stringify({ id: account.id, role: account.role, exp: Date.now() + 4 * 60 * 60 * 1000 })).toString("base64url");
  return `${payload}.${mac(payload, config.secret)}`;
}

export async function currentAccount(): Promise<{ id: string; role: Role } | null> {
  const value = (await cookies()).get(cookieName)?.value;
  const config = configuration();
  if (!value || value.length > 2048 || !config) return null;
  const [payload, tag, extra] = value.split(".");
  if (!payload || !tag || extra || !/^[0-9a-f]{64}$/.test(tag)) return null;
  const expected = mac(payload, config.secret);
  if (!timingSafeEqual(Buffer.from(tag, "hex"), Buffer.from(expected, "hex"))) return null;
  try {
    const data = JSON.parse(Buffer.from(payload, "base64url").toString()) as { id: string; role: Role; exp: number };
    if (typeof data.exp !== "number" || data.exp <= Date.now() || !roles.includes(data.role)) return null;
    if (!config.accounts.some((entry) => entry.id === data.id && entry.role === data.role)) return null;
    return { id: data.id, role: data.role };
  } catch { return null; }
}

export async function hasRole(role: Role): Promise<boolean> {
  return (await currentAccount())?.role === role;
}

export const authCookie = cookieName;
