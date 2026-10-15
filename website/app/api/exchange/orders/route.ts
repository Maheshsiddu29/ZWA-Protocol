import { NextRequest } from "next/server";
import { mutate, read } from "@/lib/exchange-route";
import { forward, remoteEnabled } from "@/lib/remote-exchange";

export const dynamic = "force-dynamic";
export const maxDuration = 30;
export const GET = (request: NextRequest) => remoteEnabled() ? forward(request, "/api/exchange/orders", "read") : read("orders");
export const POST = (request: NextRequest) => remoteEnabled() ? forward(request, "/api/exchange/orders", "mutation") : mutate(request, "buy", body =>
  typeof body.offerId === "string" && Number.isSafeInteger(body.quantity) && typeof body.idempotencyKey === "string"
    ? { offerId: body.offerId, quantity: body.quantity, idempotencyKey: body.idempotencyKey } : null);
