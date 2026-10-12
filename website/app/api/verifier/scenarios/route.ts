import { localBackend, proxyJson } from "@/lib/backend";
export const dynamic = "force-dynamic";
export async function GET() {
  return proxyJson(localBackend(process.env.ZWA_VERIFIER_URL, 3000), "/v1/demo/scenarios");
}
