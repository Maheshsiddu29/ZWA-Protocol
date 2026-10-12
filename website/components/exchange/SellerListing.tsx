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
  return <section className="exchange-panel exchange-pricing">
    <span className="exchange-eyebrow">UNLISTED INVENTORY</span>
    <h2>Offer wallet inventory</h2>
    {holdings.map(holding => <div className="exchange-price-row" key={holding.instrumentId}>
      <div><strong>{holding.name}</strong><span>{holding.units} units in your scanned wallet</span></div>
      <button className="exchange-link-button" type="button" onClick={() => list(holding)}>Offer {holding.units} units</button>
    </div>)}
    {message && <p role="alert">{message}</p>}
  </section>;
}
