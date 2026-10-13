"use client";

import { useState } from "react";
import { postJson } from "@/components/exchange/client-api";
import { zec } from "@/lib/exchange";

type Offer = { offerId: string; name: string; availableUnits: number; priceZatPerUnit: number | null };

/** The seller's fixed price per offer; it applies to new quotes only. */
export function SellerPricing({ offers, csrf }: { offers: Offer[]; csrf: string }) {
  const [prices, setPrices] = useState<Record<string, number | null>>(
    Object.fromEntries(offers.map(offer => [offer.offerId, offer.priceZatPerUnit])));
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [message, setMessage] = useState("");

  async function save(offerId: string) {
    const value = Number(draft[offerId]);
    if (!Number.isSafeInteger(value) || value < 1) { setMessage("Enter a whole number of zatoshis."); return; }
    const result = await postJson(`/api/exchange/offers/${offerId}/price`, csrf, { priceZatPerUnit: value });
    if (result.ok) { setPrices({ ...prices, [offerId]: result.data.priceZatPerUnit }); setDraft({ ...draft, [offerId]: "" }); setMessage("Saved. New quotes use this price."); }
    else setMessage(result.data?.error ?? "Price was not saved.");
  }

  return <div className="panel">
    <p className="label">Listings and prices</p>
    <p className="small muted">A quote already shown to a buyer keeps its exact price. Prices are set by you, not by an oracle.</p>
    <div className="list" style={{ marginTop: 12 }}>{offers.map(offer => <div className="list-row" key={offer.offerId}>
      <span><strong>{offer.name}</strong><small>{offer.availableUnits.toLocaleString("en-US")} units available</small></span>
      <span className="num hide-sm">{prices[offer.offerId] ? zec(prices[offer.offerId]) : "No price"}<small>{prices[offer.offerId]?.toLocaleString("en-US")} zat / unit</small></span>
      <input className="field" aria-label={`New price for ${offer.name} in zatoshis`} inputMode="numeric" placeholder="zatoshis"
             value={draft[offer.offerId] ?? ""} onChange={e => setDraft({ ...draft, [offer.offerId]: e.target.value })} style={{ minHeight: 40 }} />
      <button className="button button-small button-outline" type="button" disabled={!draft[offer.offerId]} onClick={() => save(offer.offerId)}>Save</button>
    </div>)}</div>
    {message && <p className="small" role="status" style={{ marginTop: 10 }}>{message}</p>}
  </div>;
}
