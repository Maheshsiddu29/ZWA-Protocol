"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { postJson } from "@/components/exchange/client-api";
import { stateLabel, steps, terminal, zat } from "@/lib/order-labels";
import type { PrivateOrder } from "@/lib/private-orders";

const mode = (value: string | null) => value === "HUMAN_APPROVED" ? "Human approved in wallet"
  : value === "SCRIPTED_TEST_MODE" ? "Scripted test signer (not human approval)" : "Pending";

export function OrderLive({ initial, csrf }: { initial: PrivateOrder; csrf: string }) {
  const [order, setOrder] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const done = terminal.has(order.state) || (order.state === "RECEIPT_AVAILABLE" && !!order.receipt?.durability);

  useEffect(() => {
    if (done) return;
    const timer = setInterval(async () => {
      const response = await fetch(`/api/exchange/orders/${order.id}`, { cache: "no-store" });
      if (response.ok) setOrder(await response.json());
    }, 2000);
    return () => clearInterval(timer);
  }, [order.id, done]);

  async function act(path: string, body: object = {}) {
    setBusy(true); setError("");
    const result = await postJson(path, csrf, body);
    if (result.ok) setOrder(result.data); else setError(result.data?.error ?? "Request rejected.");
    setBusy(false);
  }

  const quote = order.quote;
  const reached = new Set(order.events.map(event => event.to_state));
  const mine = order.awaitingWallet === order.viewer;
  const verb = order.viewer === "buyer" ? "Buy" : "Sell";
  return <div className="exchange-order-live">
    <section className="exchange-panel">
      <span className="exchange-eyebrow">{order.viewer.toUpperCase()} ORDER · {order.instrumentId}</span>
      <h2>{stateLabel[order.state] ?? order.state}</h2>
      <p>{verb} {order.quantity} unit{order.quantity === 1 ? "" : "s"}{quote ? ` for ${zat(quote.paymentZat)}` : ""}.</p>
      {order.note && !done && <p className="exchange-muted" role="status">{order.note}</p>}

      {order.state === "QUOTE_READY" && order.viewer === "buyer" && quote && <div className="exchange-review">
        <h3>Review your exact quote</h3>
        <div className="exchange-facts">
          <div><span>You pay</span><strong>{zat(quote.paymentZat)}</strong></div>
          <div><span>Price</span><strong>{quote.priceZatPerUnit?.toLocaleString("en-US")} zatoshis / unit · seller-fixed experimental price, not an oracle or market price</strong></div>
          <div><span>You receive</span><strong>{quote.quantity} unit{quote.quantity === 1 ? "" : "s"} of {order.instrumentId}</strong></div>
          <div><span>Network fee</span><strong>{zat(quote.networkFeeZat)}, paid by the exchange's test sponsor</strong></div>
          {quote.validUntil && <div><span>Quote valid until</span><strong>{new Date(quote.validUntil * 1000).toLocaleTimeString()}</strong></div>}
        </div>
        <div className="exchange-actions">
          <button className="exchange-button" type="button" disabled={busy}
                  onClick={() => act(`/api/exchange/orders/${order.id}/confirm`, { quoteDigest: quote.digest })}>Request purchase <span>↗</span></button>
          <button className="exchange-link-button" type="button" disabled={busy}
                  onClick={() => act(`/api/exchange/orders/${order.id}/cancel`)}>Cancel</button>
        </div>
        <small className="exchange-footnote">Requesting the purchase starts eligibility verification. You will still approve the exact trade in your wallet.</small>
      </div>}

      {order.viewer === "buyer" && !order.executionMode &&
        ["BUYER_REQUESTED", "BUYER_CONFIRMED", "PROOF_VERIFIED", "TRADE_AUTHORIZED"].includes(order.state) &&
        <div className="exchange-actions"><button className="exchange-link-button" type="button" disabled={busy}
          onClick={() => act(`/api/exchange/orders/${order.id}/cancel`)}>Cancel before wallet approval</button></div>}

      {order.awaitingWallet && <div className={`exchange-alert ${mine ? "is-action" : ""}`}>
        {mine ? <>
          <strong>Approve in your wallet companion</strong>
          <p>Your {order.viewer} wallet terminal is showing this exact trade. Check that its digest begins with <code>{quote?.digest.slice(0, 12)}</code>, then type the approval there. The default is reject.</p>
          {order.ownCompanionOnline === false && <p>Your wallet companion is not connected. Start it and keep it open.</p>}
        </> : <>
          <strong>Waiting for the {order.awaitingWallet}&apos;s wallet</strong>
          <p>The {order.awaitingWallet} reviews and approves this exact trade in their own wallet.</p>
        </>}
      </div>}

      {order.failure && <div className="exchange-alert">
        <strong>{order.state === "UNCERTAIN" ? "Held for operator review" : "No transaction was settled"}</strong>
        <p>{String(order.failure.reason ?? "").replaceAll("_", " ").toLowerCase()}</p>
      </div>}

      {order.receipt && <div className="exchange-receipt">
        <h3>Receipt</h3>
        <div className="exchange-facts">
          <div><span>{order.viewer === "buyer" ? "Received" : "Delivered"}</span><strong>{order.receipt.quantity} unit{order.receipt.quantity === 1 ? "" : "s"} of {order.receipt.instrumentId}</strong></div>
          <div><span>{order.viewer === "buyer" ? "Paid" : "Received"}</span><strong>{zat(order.receipt.paymentZat)}</strong></div>
          <div><span>Transaction</span><strong className="exchange-hash">{order.receipt.txid}</strong></div>
          <div><span>Block</span><strong>Height {order.receipt.height.toLocaleString("en-US")} · {order.receipt.confirmations} confirmations</strong></div>
          <div><span>Approval</span><strong>{mode(order.receipt.approvalMode)}</strong></div>
          {order.effects && <div><span>Your wallet change</span><strong>{order.effects.assetUnits > 0 ? "+" : ""}{order.effects.assetUnits} unit{Math.abs(order.effects.assetUnits) === 1 ? "" : "s"}{order.effects.nativeZat !== null ? ` · ${order.effects.nativeZat > 0 ? "+" : ""}${zat(order.effects.nativeZat)}` : ""}</strong></div>}
          <div><span>Restart durability</span><strong>{order.receipt.durability ? "Covered by the node's stable anchor (not consensus finality)" : "Pending"}</strong></div>
        </div>
        <Link href={order.viewer === "buyer" ? "/exchange/portfolio" : "/seller"}>View {order.viewer === "buyer" ? "portfolio" : "seller desk"} ↗</Link>
      </div>}
      {error && <p role="alert">{error}</p>}
    </section>

    <aside className="exchange-panel">
      <span className="exchange-eyebrow">PROGRESS</span>
      <ol className="exchange-steps">
        {steps.map(([state, label]) => <li key={state} className={reached.has(state) ? "is-done" : order.state === state ? "is-current" : ""}>{label}</li>)}
      </ol>
      {!order.chainActive && <p className="exchange-muted">The experimental chain is unavailable; live status is paused.</p>}
    </aside>

    <details className="exchange-tech">
      <summary>View technical details</summary>
      <div className="exchange-facts">
        <div><span>Order ID</span><strong className="exchange-hash">{order.id}</strong></div>
        <div><span>AssetBase</span><strong className="exchange-hash">{order.assetBase}</strong></div>
        {quote && <>
          <div><span>Quote digest</span><strong className="exchange-hash">{quote.digest}</strong></div>
          <div><span>Price classification</span><strong>{quote.priceClassification}</strong></div>
          <div><span>Heights</span><strong>target {quote.targetHeight} · transaction expiry {quote.expiryHeight} · group expiry {quote.groupExpiryHeight} (not enforced by pinned Zebra)</strong></div>
          <div><span>Buyer receiver</span><strong className="exchange-hash">{quote.buyerReceiverCommitment}</strong></div>
          <div><span>Seller proceeds</span><strong className="exchange-hash">{quote.sellerProceedsCommitment}</strong></div>
        </>}
        {order.proof && <>
          <div><span>Groth16 trade commitment</span><strong className="exchange-hash">{order.proof.tradeCommitment}</strong></div>
          <div><span>Authorization</span><strong>issuer {order.proof.issuerAuthentication} · provenance {order.proof.issuanceProvenance} · credential authority {order.proof.credentialAuthorityAuthentication} · eligibility {order.proof.investorEligibility} · exact trade {order.proof.exactTradeAuthorization}</strong></div>
          <div><span>Meaning</span><strong>The exact ZEC quote is verified. The price is not oracle-derived. Issuer and credential roots are disposable test authorities. Purchased-note Halo2 lineage is unsupported.</strong></div>
        </>}
        <div><span>Buyer approval</span><strong>{mode(order.buyerApprovalMode)}</strong></div>
        <div><span>Seller approval</span><strong>{mode(order.sellerApprovalMode)}</strong></div>
        {order.receipt && <div><span>Block hash</span><strong className="exchange-hash">{order.receipt.blockHash}</strong></div>}
        <div><span>Events</span><strong>{order.events.map(event => `${event.sequence}. ${event.to_state}`).join(" → ")}</strong></div>
      </div>
    </details>
  </div>;
}
