import { NextRequest } from "next/server";
import { mutate, read } from "@/lib/exchange-route";

export const dynamic = "force-dynamic";
export const GET = () => read("wallet");
export const POST = (request: NextRequest) =>
  mutate(request, "pair", body => typeof body.code === "string" && body.code.length <= 16 ? { code: body.code } : null);
