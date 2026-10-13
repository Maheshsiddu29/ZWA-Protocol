"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { postJson } from "@/components/exchange/client-api";
import { assetName, shortHash, zec, zatoshis } from "@/lib/exchange";
import { approvalLabel, failureText, stages, stateLabel, terminal } from "@/lib/order-labels";
import type { PrivateOrder } from "@/lib/private-orders";

function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

export function OrderLive({ initial, csrf, custodial }: { initial: PrivateOrder; csrf: string; custodial: boolean }) {
  const [order, setOrder] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [agreed, setAgreed] = useState(false);
  const [error, setError] = useState("");
  const done = terminal.has(order.state) || (order.state === "RECEIPT_AVAILABLE" && !!order.receipt?.durability);
  const now = useNow(order.state === "QUOTE_READY");

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
  const buyer = order.viewer === "buyer";
  const reached = new Set(order.events.map(event => event.to_state));
  const failed = terminal.has(order.state);
  const stageIndex = stages.findIndex(stage => stage.states.includes(order.state));
  const secondsLeft = quote?.validUntil ? Math.max(0, Math.floor(quote.validUntil - now / 1000)) : null;
  const name = assetName(order.instrumentId);
  const units = `${order.quantity} unit${order.quantity === 1 ? "" : "s"}`;
  const canCancel = buyer && !order.executionMode && ["BUYER_REQUESTED", "QUOTE_READY", "BUYER_CONFIRMED", "PROOF_VERIFIED", "TRADE_AUTHORIZED"].includes(order.state);
  const badge = failed ? <span className="badge badge-bad">{stateLabel[order.state]}</span>
    : order.state === "RECEIPT_AVAILABLE" ? (order.receipt ? <span className="badge badge-ok">Settled</span> : <span className="badge badge-warn">Not visible on chain</span>)
    : <span className="badge badge-blue">In progress</span>;

  return <div className="split">
    <div className="stack">
      <div className="panel">
        <div className="receipt-head"><p className="label">{buyer ? "Purchase" : "Sale"} · {name}</p>{badge}</div>
        <h1 className="big-state">{stateLabel[order.state] ?? order.state}</h1>
        <p className="muted" style={{ margin: 0 }}>{buyer ? "Buy" : "Sell"} {units}{quote ? ` for ${zec(quote.paymentZat)}` : ""}.</p>
        {order.note && !done && order.state !== "QUOTE_READY" && <p className="small muted" role="status" style={{ marginTop: 12 }}>{order.note}</p>}

        {order.state === "QUOTE_READY" && buyer && quote && <div className="review">
          <dl className="facts">
            <div><dt>You pay</dt><dd>{zec(quote.paymentZat)} <span className="muted">({zatoshis(quote.paymentZat)})</span></dd></div>
            <div><dt>You receive</dt><dd>{units} of {name}</dd></div>
            <div><dt>Price</dt><dd>{quote.priceZatPerUnit ? `${zec(quote.priceZatPerUnit)} per unit, fixed by the dealer` : "—"}</dd></div>
            <div><dt>Network fee</dt><dd>{zatoshis(quote.networkFeeZat)}, paid by the exchange</dd></div>
            <div><dt>Quote expires</dt><dd className="countdown">{secondsLeft !== null ? (secondsLeft > 0 ? `in ${Math.floor(secondsLeft / 60)}:${String(secondsLeft % 60).padStart(2, "0")}` : "now") : "—"}</dd></div>
          </dl>
          {custodial ? <p className="notice">Your demo wallet is held by this server. Confirming lets its restricted signer sign <strong>only these terms</strong>: this asset and quantity, this payment to the demo dealer, before the quote expires. It is not a signature from a wallet you control.</p>
            : <p className="notice">Confirming requests the purchase. You will still approve the exact trade in your own wallet.</p>}
          <label className="small" style={{ display: "flex", gap: 10, alignItems: "flex-start", margin: "14px 0 4px" }}>
            <input type="checkbox" checked={agreed} onChange={e => setAgreed(e.target.checked)} style={{ marginTop: 3 }} />
            <span>I confirm these exact terms.</span>
          </label>
          <div className="actions-row">
            <button className="button button-blue" type="button" disabled={busy || !agreed || secondsLeft === 0}
                    onClick={() => act(`/api/exchange/orders/${order.id}/confirm`, { quoteDigest: quote.digest })}>Confirm purchase</button>
            <button className="link-button quiet" type="button" disabled={busy} onClick={() => act(`/api/exchange/orders/${order.id}/cancel`)}>Cancel</button>
          </div>
        </div>}

        {canCancel && order.state !== "QUOTE_READY" && <div className="actions-row">
          <button className="link-button quiet" type="button" disabled={busy} onClick={() => act(`/api/exchange/orders/${order.id}/cancel`)}>Cancel order</button>
        </div>}

        {!custodial && order.awaitingWallet && <p className={`notice ${order.awaitingWallet === order.viewer ? "" : "notice-warn"}`} style={{ marginTop: 16 }}>
          {order.awaitingWallet === order.viewer
            ? <>Approve in your wallet companion. Check that the digest starts with <code>{quote?.digest.slice(0, 12)}</code>.{order.ownCompanionOnline === false && " Your companion is not connected."}</>
            : <>Waiting for the {order.awaitingWallet}&apos;s wallet approval.</>}
        </p>}

        {order.failure && <p className={`notice ${order.state === "UNCERTAIN" ? "notice-warn" : "notice-bad"}`} style={{ marginTop: 16 }}>
          {order.state === "UNCERTAIN" ? "This order is held until an operator confirms what happened on the chain. Your wallet will not be charged twice."
            : failureText(order.failure.reason)} {order.state !== "UNCERTAIN" && order.failure.transactionSubmitted === false && "Nothing was transferred."}
        </p>}
        {order.state === "RECEIPT_AVAILABLE" && !order.receipt && <p className="notice notice-warn" style={{ marginTop: 16 }}>
          {order.settlementDropped
            ? "The node restarted before this settlement became durable and its block was dropped. The swap is no longer in the chain, so nothing changed hands; the order is held for operator review."
            : "The settlement transaction is not visible in the current chain view, so its receipt is hidden. It reappears only if the node shows the same block again."}</p>}
        {error && <p className="inline-error" role="alert" style={{ marginTop: 12 }}>{error}</p>}
      </div>

      {order.receipt && <div className="panel">
        <p className="label">Receipt</p>
        <dl className="facts">
          <div><dt>{buyer ? "Received" : "Delivered"}</dt><dd>{units} of {name}</dd></div>
          <div><dt>{buyer ? "Paid" : "Received"}</dt><dd>{zec(order.receipt.paymentZat)}</dd></div>
          {order.effects && <div><dt>Wallet change</dt><dd>{order.effects.assetUnits > 0 ? "+" : ""}{order.effects.assetUnits} units{order.effects.nativeZat !== null ? `, ${order.effects.nativeZat > 0 ? "+" : ""}${zec(order.effects.nativeZat)}` : ""} (from wallet scans)</dd></div>}
          <div><dt>Transaction</dt><dd className="hash">{order.receipt.txid}</dd></div>
          <div><dt>Block</dt><dd>{order.receipt.height.toLocaleString("en-US")} · {order.receipt.confirmations.toLocaleString("en-US")} confirmation{order.receipt.confirmations === 1 ? "" : "s"}</dd></div>
          <div><dt>Signed by</dt><dd>{approvalLabel[order.receipt.approvalMode] ?? order.receipt.approvalMode}</dd></div>
          <div><dt>Durability</dt><dd>{order.receipt.durability ? "Survives a node restart (local node policy, not consensus finality)" : "Pending: extra blocks are mined shortly"}</dd></div>
        </dl>
        <div className="actions-row">
          <Link className="button button-outline button-small" href={buyer ? "/portfolio" : "/seller"}>{buyer ? "View portfolio" : "Seller desk"}</Link>
          <Link className="link-button" href={`/verify?tx=${order.receipt.txid}`}>Verify this transaction</Link>
        </div>
      </div>}
    </div>

    <aside className="stack">
      <div className="panel">
        <p className="label">Progress</p>
        <ol className="progress">
          {stages.map((stage, index) => {
            const complete = stage.states.every(state => reached.has(state)) || (stageIndex > index && !failed);
            const current = !failed && index === stageIndex && !complete;
            const failedHere = failed && index === Math.max(0, stages.findIndex(s => !s.states.every(st => reached.has(st))));
            return <li key={stage.label} className={complete ? "done" : current ? "current" : failedHere ? "failed" : ""}>{stage.label}</li>;
          })}
        </ol>
        {!order.chainActive && <p className="small muted">The experimental chain is unavailable; live status is paused.</p>}
      </div>
      <details className="tech panel">
        <summary>Technical details</summary>
        <dl className="facts">
          <div><dt>Order</dt><dd className="hash">{order.id}</dd></div>
          <div><dt>AssetBase</dt><dd className="hash">{order.assetBase}</dd></div>
          {quote && <>
            <div><dt>Quote digest</dt><dd className="hash">{quote.digest}</dd></div>
            <div><dt>Heights</dt><dd>Target {quote.targetHeight}, transaction expiry {quote.expiryHeight}</dd></div>
          </>}
          {order.proof && <>
            <div><dt>Groth16 proof</dt><dd>Issuer {order.proof.issuerAuthentication?.toLowerCase()} · issuance {order.proof.issuanceProvenance?.toLowerCase()} · eligibility {order.proof.investorEligibility?.toLowerCase()} · exact trade {order.proof.exactTradeAuthorization?.toLowerCase()}</dd></div>
            <div><dt>Trade commitment</dt><dd className="hash">{shortHash(String(order.proof.tradeCommitment ?? ""), 18, 8)}</dd></div>
          </>}
          <div><dt>Note lineage</dt><dd>Not proven for the purchased note</dd></div>
          {order.receipt && <div><dt>Block hash</dt><dd className="hash">{order.receipt.blockHash}</dd></div>}
          <div><dt>Buyer signature</dt><dd>{order.buyerApprovalMode ? approvalLabel[order.buyerApprovalMode] : "Pending"}</dd></div>
          <div><dt>Seller signature</dt><dd>{order.sellerApprovalMode ? approvalLabel[order.sellerApprovalMode] : "Pending"}</dd></div>
        </dl>
      </details>
    </aside>
  </div>;
}
