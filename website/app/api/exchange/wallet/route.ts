import { NextRequest } from "next/server";
import { mutate, read } from "@/lib/exchange-route";
import { forward, notServedHere, remoteEnabled } from "@/lib/remote-exchange";

export const dynamic = "force-dynamic";
export const maxDuration = 30;
export const GET = (request: NextRequest) => remoteEnabled() ? forward(request, "/api/exchange/wallet", "read") : read("wallet");
// Wallet pairing is for operator accounts, which a remote-mode deployment does not serve.
export const POST = (request: NextRequest) => remoteEnabled() ? notServedHere() :
  mutate(request, "pair", body => typeof body.code === "string" && body.code.length <= 16 ? { code: body.code } : null);
