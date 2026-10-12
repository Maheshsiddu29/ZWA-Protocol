import { NextRequest } from "next/server";
import { mutate, read } from "@/lib/exchange-route";

export const dynamic = "force-dynamic";
export const GET = () => read("orders");
export const POST = (request: NextRequest) => mutate(request, "buy", body =>
  typeof body.offerId === "string" && Number.isSafeInteger(body.quantity) && typeof body.idempotencyKey === "string"
    ? { offerId: body.offerId, quantity: body.quantity, idempotencyKey: body.idempotencyKey } : null);
