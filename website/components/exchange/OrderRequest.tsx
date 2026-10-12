"use client";

import { useState } from "react";

type Offer = { id: string; instrumentId: string; assetBase: string; availableUnits: number };
export function OrderRequest({ offers }: { offers: Offer[] }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState("");
  async function request(offer: Offer) {
    setBusy(true); setResult("");
    const response = await fetch("/api/exchange/orders", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ offerId: offer.id, quantity: 1,
        idempotencyKey: crypto.randomUUID() }),
    });
    if (response.ok) {
      const order = await response.json();
      setResult(`Order ${order.id} recorded as ${order.state}. A fresh executable quote and separate wallet approvals are still required.`);
    } else setResult("The live inventory request was rejected. Refresh the offers and try again.");
    setBusy(false);
  }
  return <section className="exchange-order"><div><span>Funded experimental offers</span><strong>Request a purchase</strong></div>
    {offers.length ? offers.map(offer => <div key={offer.id}><span>{offer.instrumentId} · {offer.availableUnits} units available</span><button type="button" disabled={busy} onClick={() => request(offer)}>Request one unit</button></div>) : <p>No current funded offer is available.</p>}
    {result && <p role="status">{result}</p>}
    <p className="exchange-caveat">A request reserves inventory only. The operator must prepare an exact quote, verify a genuine proof, and obtain independent wallet approvals before any transaction can be submitted.</p>
  </section>;
}
