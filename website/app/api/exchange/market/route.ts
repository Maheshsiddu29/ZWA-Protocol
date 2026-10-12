import { read } from "@/lib/exchange-route";

export const dynamic = "force-dynamic";
export const GET = () => read("market");
