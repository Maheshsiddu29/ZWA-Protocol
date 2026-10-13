import { NextRequest } from "next/server";
import { mutate } from "@/lib/exchange-route";

export const POST = (request: NextRequest) => mutate(request, "dealer-policy", body =>
  body.policy === "ACTIVE" || body.policy === "PAUSED" ? { policy: body.policy } : null);
