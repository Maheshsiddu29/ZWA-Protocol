"use client";

import { useState } from "react";
import { postJson } from "@/components/exchange/client-api";

type Offer = { offerId: string; name: string; availableUnits: number; priceZatPerUnit: number | null };

export function SellerPricing({ offers, csrf }: { offers: Offer[]; csrf: string }) {
  const [prices, setPrices] = useState<Record<string, number | null>>(
    Object.fromEntries(offers.map(offer => [offer.offerId, offer.priceZatPerUnit])));
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [message, setMessage] = useState("");

  async function save(offerId: string) {
    const value = Number(draft[offerId]);
    if (!Number.isSafeInteger(value) || value < 1) { setMessage("Enter a whole number of zatoshis."); return; }
    const result = await postJson(`/api/exchange/offers/${offerId}/price`, csrf, { priceZatPerUnit: value });
    if (result.ok) { setPrices({ ...prices, [offerId]: result.data.priceZatPerUnit }); setMessage("Price saved for new quotes."); }
    else setMessage(result.data?.error ?? "Price was not saved.");
  }

  return <section className="exchange-panel exchange-pricing">
    <span className="exchange-eyebrow">OFFER PRICING</span>
    <h2>Your fixed experimental prices</h2>
    <p>Each new quote uses the price set here. A quote already shown to a buyer keeps its exact price. These are seller-set test prices, not oracle or market prices.</p>
    {offers.map(offer => <div className="exchange-price-row" key={offer.offerId}>
      <div><strong>{offer.name}</strong><span>{offer.availableUnits} units offered · current price {prices[offer.offerId] ? `${prices[offer.offerId]!.toLocaleString("en-US")} zatoshis / unit` : "not set"}</span></div>
      <label htmlFor={`price-${offer.offerId}`}>Zatoshis per unit</label>
      <input id={`price-${offer.offerId}`} inputMode="numeric" value={draft[offer.offerId] ?? ""}
             onChange={e => setDraft({ ...draft, [offer.offerId]: e.target.value })} placeholder="1000" />
      <button className="exchange-link-button" type="button" onClick={() => save(offer.offerId)}>Save price</button>
    </div>)}
    {message && <p role="status">{message}</p>}
  </section>;
}
