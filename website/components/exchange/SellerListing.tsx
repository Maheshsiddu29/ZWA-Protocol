"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { postJson } from "@/components/exchange/client-api";

type Holding = { instrumentId: string; name: string; units: number };

/** Offer scanned, unlisted wallet inventory. The server rechecks the live scan. */
export function SellerListing({ holdings, scanHeight, csrf }: { holdings: Holding[]; scanHeight: number; csrf: string }) {
  const router = useRouter();
  const [message, setMessage] = useState("");
  async function list(holding: Holding) {
    const result = await postJson("/api/exchange/offers", csrf, { instrumentId: holding.instrumentId,
      capacity: holding.units, idempotencyKey: `list-${holding.instrumentId}-${scanHeight}` });
    if (result.ok) router.refresh(); else setMessage(result.data?.error ?? "Offer was not created.");
  }
  if (!holdings.length) return null;
  return <div className="panel">
    <p className="label">Unlisted inventory</p>
    <div className="list" style={{ marginTop: 12 }}>{holdings.map(holding => <div className="list-row" key={holding.instrumentId}>
      <span><strong>{holding.name}</strong><small>{holding.units} units in your scanned wallet</small></span>
      <span className="hide-sm" /><span className="hide-sm" />
      <button className="button button-small button-outline" type="button" onClick={() => list(holding)}>Offer {holding.units} units</button>
    </div>)}</div>
    {message && <p className="inline-error" role="alert">{message}</p>}
  </div>;
}
