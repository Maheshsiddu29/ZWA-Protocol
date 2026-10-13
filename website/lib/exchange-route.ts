import "server-only";
import { NextRequest, NextResponse } from "next/server";
import { exchangeAction, mutationAllowed, readSmallJson, type BridgeResult } from "@/lib/private-orders";

const noStore = { "Cache-Control": "no-store" };

export function respond<T>(result: BridgeResult<T> | null, success = 200) {
  if (!result) return NextResponse.json({ error: "Exchange access unavailable" }, { status: 403, headers: noStore });
  if (!result.ok) return NextResponse.json({ error: result.error }, { status: 409, headers: noStore });
  return NextResponse.json(result.data, { status: success, headers: noStore });
}

export async function read(action: string, payload: Record<string, unknown> = {}) {
  return respond(await exchangeAction(action, payload));
}

/** Run a fixed mutation after origin, size and CSRF checks. */
export async function mutate(request: NextRequest, action: string,
                             shape: (body: Record<string, unknown>) => Record<string, unknown> | null) {
  if (!await mutationAllowed(request)) return NextResponse.json({ error: "Request rejected" }, { status: 403, headers: noStore });
  const body = await readSmallJson(request);
  const payload = body ? shape(body) : null;
  if (!payload) return NextResponse.json({ error: "Invalid request" }, { status: 400, headers: noStore });
  return respond(await exchangeAction(action, payload), 200);
}
