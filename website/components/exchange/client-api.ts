// Same-origin JSON calls for exchange mutations; the CSRF token is per login.
// Never throws: status 0 means no HTTP answer (network failure or timeout), so the outcome is unknown.
export async function postJson(path: string, csrf: string, body: object = {}, timeoutMs = 45000): Promise<{ ok: boolean; status: number; data: any }> {
  try {
    const response = await fetch(path, { method: "POST", credentials: "same-origin", signal: AbortSignal.timeout(timeoutMs),
      headers: { "content-type": "application/json", "x-zwa-csrf": csrf }, body: JSON.stringify(body) });
    let data: any = null;
    try { data = await response.json(); } catch { /* empty body */ }
    return { ok: response.ok, status: response.status, data };
  } catch {
    return { ok: false, status: 0, data: null };
  }
}

/** A read; null when there was no usable answer. */
export async function getJson(path: string, timeoutMs = 20000): Promise<{ status: number; data: any } | null> {
  try {
    const response = await fetch(path, { cache: "no-store", credentials: "same-origin", signal: AbortSignal.timeout(timeoutMs) });
    let data: any = null;
    try { data = await response.json(); } catch { /* empty body */ }
    return { status: response.status, data };
  } catch { return null; }
}

/** True when the server said the result of a mutation is unknown (or never answered). */
export const outcomeUnknown = (result: { status: number; data: any }) =>
  result.status === 0 || result.status >= 500 || result.data?.outcome === "unknown";
