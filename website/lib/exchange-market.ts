import "server-only";

const feedId = "765d2ba906dbc32ca17cc11f5310a89e9ee1f6420508c63861f2f8ba4ee34bb2";

function displayDecimal(mantissa: bigint, exponent: number): string {
  if (exponent === 0) return mantissa.toString();
  const places = -exponent;
  const raw = mantissa.toString().padStart(places + 1, "0");
  return `${raw.slice(0, -places)}.${raw.slice(-places)}`.replace(/0+$/, "").replace(/\.$/, "");
}

export async function goldReference(): Promise<{ value: string; observedAt: string; confidence: string; source: string; trust: string } | null> {
  const key = process.env.PYTH_API_KEY;
  if (!key) return null;
  try {
    const url = `https://pyth.dourolabs.app/hermes/v2/updates/price/latest?ids%5B%5D=0x${feedId}`;
    const response = await fetch(url, { headers: { Authorization: `Bearer ${key}` }, cache: "no-store", signal: AbortSignal.timeout(4000) });
    if (!response.ok) return null;
    const content = await response.text();
    if (content.length > 262144) return null;
    const parsed = JSON.parse(content).parsed;
    if (!Array.isArray(parsed) || parsed.length !== 1 || parsed[0]?.id !== feedId) return null;
    const price = parsed[0]?.price;
    if (!/^[1-9][0-9]*$/.test(price?.price) || !/^(0|[1-9][0-9]*)$/.test(price?.conf)) return null;
    if (!Number.isInteger(price.expo) || price.expo < -12 || price.expo > 0) return null;
    const observed = price.publish_time;
    const now = Math.floor(Date.now() / 1000);
    if (!Number.isInteger(observed) || observed > now + 30 || now - observed > 3600) return null;
    const value = BigInt(price.price), confidence = BigInt(price.conf);
    if (confidence * 10n > value) return null;
    return { value: displayDecimal(value, price.expo), confidence: displayDecimal(confidence, price.expo),
      observedAt: new Date(observed * 1000).toISOString(), source: "Pyth Core XAU/USD", trust: "HTTPS transport; signed update not independently verified" };
  } catch { return null; }
}
