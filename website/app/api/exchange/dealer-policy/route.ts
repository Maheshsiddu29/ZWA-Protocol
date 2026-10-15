import { NextRequest } from "next/server";
import { mutate } from "@/lib/exchange-route";
import { notServedHere, remoteEnabled } from "@/lib/remote-exchange";

export const POST = (request: NextRequest) => remoteEnabled() ? notServedHere() : mutate(request, "dealer-policy", body =>
  body.policy === "ACTIVE" || body.policy === "PAUSED" ? { policy: body.policy } : null);
