// Same-origin JSON calls for exchange mutations; the CSRF token is per login.
export async function postJson(path: string, csrf: string, body: object = {}): Promise<{ ok: boolean; data: any }> {
  const response = await fetch(path, { method: "POST", credentials: "same-origin",
    headers: { "content-type": "application/json", "x-zwa-csrf": csrf }, body: JSON.stringify(body) });
  let data: any = null;
  try { data = await response.json(); } catch { /* empty body */ }
  return { ok: response.ok, data };
}
