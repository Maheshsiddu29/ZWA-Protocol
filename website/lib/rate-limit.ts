import "server-only";

type Bucket = { tokens: number; updated: number };
const buckets = new Map<string, Bucket>();

/**
 * In-process token bucket for one web instance. It smooths bursts in front of
 * the backend; the durable, authoritative caps (sessions per hour, per client,
 * orders per wallet, sponsored fees) are enforced in the exchange ledger.
 */
export function allow(key: string, capacity: number, refillPerMinute: number): boolean {
  const now = Date.now();
  const bucket = buckets.get(key) ?? { tokens: capacity, updated: now };
  bucket.tokens = Math.min(capacity, bucket.tokens + ((now - bucket.updated) / 60000) * refillPerMinute);
  bucket.updated = now;
  const allowed = bucket.tokens >= 1;
  if (allowed) bucket.tokens -= 1;
  buckets.set(key, bucket);
  if (buckets.size > 10000) {
    for (const [name, entry] of buckets) if (now - entry.updated > 600000) buckets.delete(name);
  }
  return allowed;
}
