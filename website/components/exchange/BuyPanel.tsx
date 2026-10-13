"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { postJson } from "@/components/exchange/client-api";
import { TryDemoButton } from "@/components/exchange/TryDemoButton";
import { zec, zatoshis } from "@/lib/exchange";
import type { MarketOffer, WalletStatus } from "@/lib/private-orders";

type Props = {
  offer: MarketOffer | null; viewer: { kind: "account" | "demo"; role: "buyer" | "seller" } | null;
  wallet: WalletStatus | null; demo: boolean; csrf: string; chainActive: boolean;
  limits: { dealerPolicy: "ACTIVE" | "PAUSED"; maxUnitsPerOrder: number; maxPaymentZatPerOrder: number } | null;
};

export function BuyPanel({ offer, viewer, wallet, demo, csrf, chainActive, limits }: Props) {
  const router = useRouter();
  const price = offer?.priceZatPerUnit ?? null;
  const maxUnits = Math.max(0, Math.min(offer?.availableUnits ?? 0, limits?.maxUnitsPerOrder ?? 1000,
    price && limits ? Math.floor(limits.maxPaymentZatPerOrder / price) : 1000));
  const [quantity, setQuantity] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // One key per purchase intent: a double click or retry returns the same order.
  const key = useRef(`buy-${crypto.randomUUID()}`);
  const setQty = (value: number) => { setQuantity(value); key.current = `buy-${crypto.randomUUID()}`; };
  const valid = Number.isSafeInteger(quantity) && quantity >= 1 && quantity <= maxUnits;

  async function buy() {
    if (!offer || !price || !valid || busy) return;
    setBusy(true); setError("");
    const result = await postJson("/api/exchange/orders", csrf, { offerId: offer.offerId, quantity, idempotencyKey: key.current });
    if (result.ok && /^[0-9a-f]{32}$/.test(result.data?.id)) { router.push(`/orders/${result.data.id}`); return; }
    setError(result.data?.error ?? "The purchase could not be started.");
    setBusy(false);
  }

  const head = <>
    <p className="label">Buy</p>
    <div className="figure"><strong>{price ? zec(price) : "—"}</strong><span>per unit</span></div>
  </>;
  if (!chainActive) return <div className="panel">{head}<p className="notice notice-warn">The experimental chain is unavailable. Trading resumes when it is back.</p></div>;
  if (!offer || !price) return <div className="panel">{head}<p className="muted">This asset has no live offer right now.</p></div>;
  if (limits?.dealerPolicy === "PAUSED") return <div className="panel">{head}<p className="notice notice-warn">The dealer has paused new sales.</p></div>;
  if (viewer?.role === "seller") return <div className="panel">{head}<p className="muted">You are signed in as the seller.</p><Link className="button button-outline button-small" href="/seller">Open the seller desk</Link></div>;
  if (!viewer) return <div className="panel">{head}
    <p className="muted">{demo ? "Start a demo session to get a funded test wallet. It takes a few seconds." : "Sign in to buy."}</p>
    {demo ? <TryDemoButton className="button button-blue button-wide" label="Start demo to buy" /> : <Link className="button button-blue button-wide" href="/sign-in">Sign in</Link>}
  </div>;
  const ready = viewer.kind === "demo" ? wallet?.state === "READY" : !!wallet?.paired;
  if (!ready) return <div className="panel">{head}
    <p className="muted">{viewer.kind === "demo" ? "Your demo wallet is still being prepared." : "Pair your wallet before buying."}</p>
    <Link className="button button-blue button-wide" href="/wallet">Open wallet</Link></div>;
  if (wallet?.openOrderId) return <div className="panel">{head}
    <p className="muted">You have an order in progress. Finish or cancel it first.</p>
    <Link className="button button-blue button-wide" href={`/orders/${wallet.openOrderId}`}>View order</Link></div>;
  if (wallet?.limits?.maxOrders !== undefined && (wallet.ordersUsed ?? 0) >= wallet.limits.maxOrders) return <div className="panel">{head}
    <p className="muted">This demo wallet has reached its {wallet.limits.maxOrders}-order limit.</p></div>;
  const total = valid ? price * quantity : null;
  const insufficient = total !== null && wallet?.nativeZat != null && wallet.nativeZat < total;
  return <div className="panel">
    {head}
    <label className="field-label" htmlFor="quantity">Quantity</label>
    <div className="qty">
      <button type="button" aria-label="Decrease quantity" disabled={quantity <= 1} onClick={() => setQty(quantity - 1)}>−</button>
      <input id="quantity" type="number" inputMode="numeric" min={1} max={maxUnits} value={quantity}
             onChange={e => setQty(Number(e.target.value))} />
      <button type="button" aria-label="Increase quantity" disabled={quantity >= maxUnits} onClick={() => setQty(quantity + 1)}>+</button>
    </div>
    <div className="total-line"><span>Estimated total</span><strong>{total !== null ? zec(total) : "—"}</strong></div>
    <div className="total-line"><span>Network fee</span><strong>Paid by the exchange</strong></div>
    {wallet?.nativeZat != null && <div className="total-line"><span>Wallet balance</span><strong>{zec(wallet.nativeZat)}</strong></div>}
    <button className="button button-blue button-wide" type="button" style={{ marginTop: 14 }}
            disabled={busy || !valid || insufficient} onClick={buy}>{busy ? "Requesting quote…" : "Get exact quote"}</button>
    {error && <p className="inline-error" role="alert" style={{ marginTop: 10 }}>{error}</p>}
    <p className="small muted" style={{ marginTop: 14 }}>
      {offer.availableUnits.toLocaleString("en-US")} units available · up to {maxUnits} per order.
      {total !== null && ` Total ${zatoshis(total)}.`} You review the exact quote before anything is signed.
    </p>
  </div>;
}
