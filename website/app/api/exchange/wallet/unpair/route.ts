import { NextRequest } from "next/server";
import { mutate } from "@/lib/exchange-route";

export const POST = (request: NextRequest) => mutate(request, "unpair", () => ({}));
