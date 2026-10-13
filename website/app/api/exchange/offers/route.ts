import { NextRequest } from "next/server";
import { mutate } from "@/lib/exchange-route";

export const dynamic = "force-dynamic";
export const POST = (request: NextRequest) => mutate(request, "create-offer", body =>
  typeof body.instrumentId === "string" && Number.isSafeInteger(body.capacity) && typeof body.idempotencyKey === "string"
    ? { instrumentId: body.instrumentId, capacity: body.capacity, idempotencyKey: body.idempotencyKey } : null);
