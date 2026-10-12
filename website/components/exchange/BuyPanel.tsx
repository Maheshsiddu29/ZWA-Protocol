"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { postJson } from "@/components/exchange/client-api";
import { zat } from "@/lib/order-labels";

type Offer = { offerId: string; availableUnits: number; priceZatPerUnit: number | null; priceClassification: string | null };

export function BuyPanel({ offer, csrf, paired }: { offer: Offer; csrf: string; paired: boolean }) {
  const router = useRouter();
  const [quantity, setQuantity] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // One key per Buy intent: a double click or retry returns the same order.
  const key = useRef(`buy-${crypto.randomUUID()}`);
  const price = offer.priceZatPerUnit;
  const valid = Number.isSafeInteger(quantity) && quantity >= 1 && quantity <= offer.availableUnits;

  async function buy() {
    if (!price || !valid || busy) return;
    setBusy(true); setError("");
    const result = await postJson("/api/exchange/orders", csrf,
      { offerId: offer.offerId, quantity, idempotencyKey: key.current });
    if (result.ok && /^[0-9a-f]{32}$/.test(result.data?.id)) router.push(`/exchange/orders/${result.data.id}`);
    else { setError(result.data?.error ?? "The purchase could not be started."); setBusy(false); }
  }

  if (!price) return <p className="exchange-muted">The seller has not set a price for this offer yet.</p>;
  return <div className="exchange-buy">
    <div className="exchange-price"><strong>{price.toLocaleString("en-US")}</strong><span>zatoshis per unit · seller-fixed experimental price</span></div>
    <label htmlFor="quantity">Quantity</label>
    <input id="quantity" type="number" min={1} max={offer.availableUnits} value={quantity}
           onChange={e => { setQuantity(Number(e.target.value)); key.current = `buy-${crypto.randomUUID()}`; }} />
    <p className="exchange-muted">{offer.availableUnits} units available · estimated total {valid ? zat(price * quantity) : "—"}</p>
    {paired
      ? <button className="exchange-button" type="button" disabled={busy || !valid} onClick={buy}>{busy ? "Starting…" : "Buy"} <span>↗</span></button>
      : <a className="exchange-button" href="/exchange/wallet">Pair your wallet to buy <span>↗</span></a>}
    {error && <p role="alert">{error}</p>}
    <small className="exchange-footnote">Buy asks the exchange for an exact quote. Nothing is signed until you approve it in your own wallet.</small>
  </div>;
}
