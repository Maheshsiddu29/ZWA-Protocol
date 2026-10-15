"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getJson, postJson } from "@/components/exchange/client-api";
import { classify, clearIntent, loadIntent, newIntent, saveIntent, submit, type Intent, type OrderSummary, type Outcome } from "@/components/exchange/purchase";
import { TryDemoButton } from "@/components/exchange/TryDemoButton";
import { assetName, zec, zatoshis } from "@/lib/exchange";
import type { MarketOffer, WalletStatus } from "@/lib/private-orders";

type Props = {
  offer: MarketOffer | null; viewer: { kind: "account" | "demo"; role: "buyer" | "seller" } | null;
  /** Scopes the stored purchase intent to this participant. */
  account: string;
  wallet: WalletStatus | null; demo: boolean; csrf: string; chainActive: boolean;
  limits: { dealerPolicy: "ACTIVE" | "PAUSED"; maxUnitsPerOrder: number; maxPaymentZatPerOrder: number } | null;
};

const storage = () => { try { return window.localStorage; } catch { return null; } };
const sleep = (ms: number) => new Promise<void>(resolve => setTimeout(resolve, ms));

async function buyerOrders(): Promise<OrderSummary[] | null> {
  const reply = await getJson("/api/exchange/orders");
  return reply?.status === 200 && Array.isArray(reply.data?.orders) ? reply.data.orders : null;
}

export function BuyPanel({ offer, viewer, account, wallet, demo, csrf, chainActive, limits }: Props) {
  const router = useRouter();
  const price = offer?.priceZatPerUnit ?? null;
  const maxUnits = Math.max(0, Math.min(offer?.availableUnits ?? 0, limits?.maxUnitsPerOrder ?? 1000,
    price && limits ? Math.floor(limits.maxPaymentZatPerOrder / price) : 1000));
  const [quantity, setQuantity] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // An unresolved purchase from this browser (survives a reload). While it exists, no new key is made.
  const [pending, setPending] = useState<Intent | null>(null);
  const [unresolved, setUnresolved] = useState<Extract<Outcome, { kind: "unknown" }> | null>(null);
  const valid = Number.isSafeInteger(quantity) && quantity >= 1 && quantity <= maxUnits;

  useEffect(() => {
    if (!account) return;
    const saved = loadIntent(storage(), account);
    setPending(saved);
    if (!saved) return;
    setQuantity(saved.quantity);
    // A purchase left unresolved (reload, closed tab): resolve it by reading orders, never by buying.
    setBusy(true);
    buyerOrders().then(orders => finish(saved, classify(saved, orders)));
  }, [account]);

  function finish(intent: Intent, outcome: Outcome) {
    if (outcome.kind === "accepted") {
      clearIntent(storage(), account);
      router.push(`/orders/${outcome.orderId}`);
      return;
    }
    if (outcome.kind === "refused") {
      clearIntent(storage(), account);
      setPending(null); setError(outcome.error);
    } else {
      saveIntent(storage(), account, intent);
      setPending(intent); setUnresolved(outcome);
    }
    setBusy(false);
  }

  async function run(intent: Intent) {
    setBusy(true); setError(""); setUnresolved(null);
    const result = await submit(intent, {
      post: async body => { const r = await postJson("/api/exchange/orders", csrf, body); return r.status === 0 ? null : r; },
      orders: buyerOrders,
      remember: next => { saveIntent(storage(), account, next); setPending(next); },
      sleep,
    });
    finish(result.intent, result.outcome);
  }

  async function buy() {
    if (!offer || !price || !valid || busy || pending) return;
    setBusy(true); setError("");
    // Snapshot existing orders first, so a lost answer can later be matched to the one new order.
    const before = await buyerOrders();
    const intent = newIntent(offer.offerId, offer.instrumentId, quantity, before ? before.map(o => o.id) : null,
                             () => crypto.randomUUID());
    saveIntent(storage(), account, intent); // durably recorded before anything is sent
    setPending(intent);
    await run(intent);
  }

  async function lookup() {
    if (!pending) return;
    setBusy(true); setError("");
    const outcome = classify(pending, await buyerOrders());
    finish(pending, outcome);
  }

  function discard() {
    clearIntent(storage(), account);
    setPending(null); setUnresolved(null);
  }

  const head = <>
    <p className="label">Buy</p>
    <div className="figure"><strong>{price ? zec(price) : "—"}</strong><span>per unit</span></div>
    <p className="small muted network-line"><span className="status-dot" aria-hidden="true" />Network: Zcash regtest (experimental ZSA test chain)</p>
  </>;
  if (!chainActive) return <div className="panel">{head}<p className="notice notice-warn">The experimental chain is unavailable. Trading resumes when it is back.</p></div>;
  if (!offer || !price) return <div className="panel">{head}<p className="muted">This asset has no live offer right now.</p></div>;
  if (viewer?.role === "seller") return <div className="panel">{head}<p className="muted">You are signed in as the seller.</p><Link className="button button-outline button-small" href="/seller">Open the seller desk</Link></div>;
  if (!viewer) return <div className="panel">{head}
    <p className="muted">{demo ? "Launch the demo to get a funded test wallet. It takes a few seconds." : "Sign in to buy."}</p>
    {demo ? <TryDemoButton className="button button-blue button-wide" label="Launch demo to buy" /> : <Link className="button button-blue button-wide" href="/sign-in">Sign in</Link>}
  </div>;

  // Recovery comes before every other gate: an unresolved purchase must be resolved, not repeated.
  if (pending) {
    const here = pending.offerId === offer.offerId;
    return <div className="panel">{head}
      <p className="notice notice-warn" role="status">
        {busy ? "Submitting your purchase. If the connection drops, it is resent with the same purchase key, so it can never create a second order."
          : <>We could not confirm whether the exchange accepted your purchase of <strong>{pending.quantity} unit{pending.quantity === 1 ? "" : "s"} of {assetName(pending.instrumentId)}</strong>. {unresolved?.reason}</>}
      </p>
      {!busy && <>
        <p className="small muted" style={{ marginTop: 12 }}>Nothing is signed or paid until you confirm an exact quote on the order page. Checking again resends the <strong>same</strong> purchase key: if the order exists you are taken to it, otherwise it is created once.</p>
        {unresolved?.candidates.length ? <div className="list" style={{ margin: "10px 0" }}>{unresolved.candidates.map(id =>
          <Link className="list-row" key={id} href={`/orders/${id}`}><span><strong>Order {id.slice(0, 8)}…</strong><small>Open to check whether it is this purchase</small></span></Link>)}</div> : null}
        <div className="actions-row" style={{ marginTop: 12 }}>
          {here ? <button className="button button-blue" type="button" onClick={() => run(pending)}>Check again (same key)</button>
            : <Link className="button button-blue" href={`/exchange/${pending.instrumentId}`}>Go to that asset</Link>}
          <button className="link-button" type="button" onClick={lookup}>Look up my orders</button>
          {unresolved && !unresolved.candidates.length && <button className="link-button quiet" type="button" onClick={discard}>Forget this attempt</button>}
        </div>
        {unresolved && !unresolved.candidates.length && <p className="small muted" style={{ marginTop: 10 }}>If you forget it and the earlier request still arrives, its order appears under Portfolio. It waits for your confirmation and expires if you don&apos;t confirm, so nothing is paid twice.</p>}
      </>}
      {error && <p className="inline-error" role="alert" style={{ marginTop: 10 }}>{error}</p>}
    </div>;
  }

  if (limits?.dealerPolicy === "PAUSED") return <div className="panel">{head}<p className="notice notice-warn">The dealer has paused new sales.</p></div>;
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
      <button type="button" aria-label="Decrease quantity" disabled={busy || quantity <= 1} onClick={() => setQuantity(quantity - 1)}>−</button>
      <input id="quantity" type="number" inputMode="numeric" min={1} max={maxUnits} value={quantity} disabled={busy}
             onChange={e => setQuantity(Number(e.target.value))} />
      <button type="button" aria-label="Increase quantity" disabled={busy || quantity >= maxUnits} onClick={() => setQuantity(quantity + 1)}>+</button>
    </div>
    <div className="total-line"><span>Estimated total</span><strong>{total !== null ? zec(total) : "—"}</strong></div>
    <div className="total-line"><span>Network fee</span><strong>Paid by the exchange</strong></div>
    {wallet?.nativeZat != null && <div className="total-line"><span>Wallet balance</span><strong>{zec(wallet.nativeZat)}</strong></div>}
    {wallet?.holdings && <div className="total-line"><span>You hold</span><strong>{(wallet.holdings.find(h => h.instrumentId === offer.instrumentId)?.units ?? 0).toLocaleString("en-US")} units</strong></div>}
    <button className="button button-blue button-wide" type="button" style={{ marginTop: 14 }}
            disabled={busy || !valid || insufficient} onClick={buy}>{busy ? "Requesting quote…" : "Buy · get exact quote"}</button>
    {insufficient && <p className="small inline-error" style={{ marginTop: 8 }}>Your wallet balance is below this total.</p>}
    {error && <p className="inline-error" role="alert" style={{ marginTop: 10 }}>{error}</p>}
    <p className="small muted" style={{ marginTop: 14 }}>
      {offer.availableUnits.toLocaleString("en-US")} units available · up to {maxUnits} per order.
      {total !== null && ` Total ${zatoshis(total)}.`} You review the exact quote before anything is signed.
    </p>
  </div>;
}
