"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { getJson, outcomeUnknown, postJson } from "@/components/exchange/client-api";
import { assetName, shortHash, zec, zatoshis } from "@/lib/exchange";
import { approvalLabel, failureText, progress, stateLabel, terminal, verdict } from "@/lib/order-labels";
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

const ended = (reply: { status: number; data: any } | null) =>
  !!reply && (reply.status === 403 || (reply.status === 409 && /session expired|start a new demo/.test(String(reply.data?.error))));

export function OrderLive({ initial, csrf, custodial }: { initial: PrivateOrder; csrf: string; custodial: boolean }) {
  const [order, setOrder] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [agreed, setAgreed] = useState(false);
  const [error, setError] = useState("");
  const [info, setInfo] = useState("");
  const [updatedAt, setUpdatedAt] = useState(() => Date.now());
  const [failures, setFailures] = useState(0);
  const [sessionEnded, setSessionEnded] = useState(false);
  const result = verdict(order);
  const done = terminal.has(order.state) || result === "VERIFIED";
  const now = useNow(order.state === "QUOTE_READY" || failures > 0);

  const refresh = useCallback(async () => {
    const reply = await getJson(`/api/exchange/orders/${order.id}`);
    if (reply?.status === 200 && reply.data?.id === order.id) {
      setOrder(reply.data); setUpdatedAt(Date.now()); setFailures(0);
      return reply.data as PrivateOrder;
    }
    if (ended(reply)) setSessionEnded(true); else setFailures(count => count + 1);
    return null;
  }, [order.id]);

  useEffect(() => {
    if (done || sessionEnded) return;
    const timer = setInterval(refresh, failures ? Math.min(2000 * 2 ** failures, 15000) : 2000);
    return () => clearInterval(timer);
  }, [done, sessionEnded, failures, refresh]);

  /** Confirm and cancel are safe to repeat (the ledger answers the same way); an unknown result is re-read. */
  async function act(path: string, body: object = {}) {
    setBusy(true); setError(""); setInfo("");
    const reply = await postJson(path, csrf, body);
    if (reply.ok && reply.data?.id === order.id) { setOrder(reply.data); setUpdatedAt(Date.now()); }
    else if (outcomeUnknown(reply)) {
      setInfo("The exchange did not confirm the result. Checking the live order status…");
      const fresh = await refresh();
      setInfo(fresh ? "Status refreshed from the exchange. It shows what actually happened." : "Live status is unavailable. Your request is safe to repeat; it never creates a second order.");
    } else if (ended(reply)) setSessionEnded(true);
    else { setError(reply.data?.error ?? "Request rejected."); await refresh(); }
    setBusy(false);
  }

  const quote = order.quote;
  const buyer = order.viewer === "buyer";
  const failed = terminal.has(order.state);
  const stages = progress(order);
  const secondsLeft = quote?.validUntil ? Math.max(0, Math.floor(quote.validUntil - now / 1000)) : null;
  const name = assetName(order.instrumentId);
  const units = `${order.quantity} unit${order.quantity === 1 ? "" : "s"}`;
  const canCancel = buyer && !order.executionMode && ["BUYER_REQUESTED", "QUOTE_READY", "BUYER_CONFIRMED", "PROOF_VERIFIED", "TRADE_AUTHORIZED"].includes(order.state);
  const txid = typeof order.failure?.txid === "string" ? order.failure.txid : null;
  const headline = result === "VERIFIED" ? "Settlement verified"
    : result === "CONFIRMING" ? "Included and reconciled"
    : result === "NOT_VISIBLE" ? "Settlement not visible on chain"
    : stateLabel[order.state] ?? order.state;
  const badge = result === "VERIFIED" ? <span className="badge badge-ok"><span className="status-dot" />Settlement verified</span>
    : result === "CONFIRMING" ? <span className="badge badge-blue">Confirming durability</span>
    : result === "NOT_VISIBLE" || result === "UNCERTAIN" ? <span className="badge badge-warn">{result === "UNCERTAIN" ? "Unresolved" : "Not visible"}</span>
    : failed ? <span className="badge badge-bad">{stateLabel[order.state]}</span>
    : <span className="badge badge-blue">In progress</span>;
  const staleFor = failures > 0 ? Math.max(0, Math.round((now - updatedAt) / 1000)) : 0;

  return <div className="split">
    <div className="stack">
      <div className="panel">
        <div className="receipt-head"><p className="label">{buyer ? "Purchase" : "Sale"} · {name}</p>{badge}</div>
        <h1 className="big-state">{headline}</h1>
        <p className="muted" style={{ margin: 0 }}>{buyer ? "Buy" : "Sell"} {units}{quote ? ` for ${zec(quote.paymentZat)}` : ""} · Zcash regtest</p>
        {order.note && !done && order.state !== "QUOTE_READY" && <p className="small muted" role="status" style={{ marginTop: 12 }}>{order.note}</p>}
        {result === "CONFIRMING" && <p className="notice" style={{ marginTop: 16 }}>The swap is in a block and both wallets were rescanned with exactly the quoted changes. The exchange is mining extra blocks so it survives a node restart; &ldquo;Settlement verified&rdquo; appears after that.</p>}
        {result === "VERIFIED" && <p className="notice notice-ok" style={{ marginTop: 16 }}>The exchange just re-read the block holding this transaction from the node, and both wallets&apos; rescans match the exact quote. Regtest test assets only; local node policy, not public-network finality.</p>}

        {order.state === "QUOTE_READY" && buyer && quote && <div className="review">
          <dl className="facts">
            <div><dt>You pay</dt><dd>{zec(quote.paymentZat)} <span className="muted">({zatoshis(quote.paymentZat)})</span></dd></div>
            <div><dt>You receive</dt><dd>{units} of {name}</dd></div>
            <div><dt>Price</dt><dd>{quote.priceZatPerUnit ? `${zec(quote.priceZatPerUnit)} per unit, fixed by the dealer` : "—"}</dd></div>
            <div><dt>Network fee</dt><dd>{zatoshis(quote.networkFeeZat)}, paid by the exchange</dd></div>
            <div><dt>Network</dt><dd>Zcash {quote.network} (experimental ZSA test chain)</dd></div>
            <div><dt>Quote expires</dt><dd className="countdown">{secondsLeft !== null ? (secondsLeft > 0 ? `in ${Math.floor(secondsLeft / 60)}:${String(secondsLeft % 60).padStart(2, "0")}` : "now") : "—"}</dd></div>
          </dl>
          {custodial ? <p className="notice">Your demo wallet is held by this server. Confirming lets its restricted signer sign <strong>only these terms</strong>: this asset and quantity, this payment to the demo dealer, before the quote expires. It is not a signature from a wallet you control.</p>
            : <p className="notice">Confirming requests the purchase. You will still approve the exact trade in your own wallet.</p>}
          <label className="small" style={{ display: "flex", gap: 10, alignItems: "flex-start", margin: "14px 0 4px" }}>
            <input type="checkbox" checked={agreed} onChange={e => setAgreed(e.target.checked)} style={{ marginTop: 3 }} />
            <span>I confirm these exact terms.</span>
          </label>
          <div className="actions-row">
            <button className="button button-blue" type="button" disabled={busy || !agreed || secondsLeft === 0 || sessionEnded}
                    onClick={() => act(`/api/exchange/orders/${order.id}/confirm`, { quoteDigest: quote.digest })}>{busy ? "Confirming…" : "Confirm purchase"}</button>
            <button className="link-button quiet" type="button" disabled={busy || sessionEnded} onClick={() => act(`/api/exchange/orders/${order.id}/cancel`)}>Cancel</button>
          </div>
        </div>}

        {canCancel && order.state !== "QUOTE_READY" && <div className="actions-row">
          <button className="link-button quiet" type="button" disabled={busy || sessionEnded} onClick={() => act(`/api/exchange/orders/${order.id}/cancel`)}>Cancel order</button>
        </div>}

        {!custodial && order.awaitingWallet && <p className={`notice ${order.awaitingWallet === order.viewer ? "" : "notice-warn"}`} style={{ marginTop: 16 }}>
          {order.awaitingWallet === order.viewer
            ? <>Approve in your wallet companion. Check that the digest starts with <code>{quote?.digest.slice(0, 12)}</code>.{order.ownCompanionOnline === false && " Your companion is not connected."}</>
            : <>Waiting for the {order.awaitingWallet}&apos;s wallet approval.</>}
        </p>}

        {order.state === "UNCERTAIN" && <div className="notice notice-warn" style={{ marginTop: 16 }} role="status">
          <p style={{ margin: 0 }}><strong>Unresolved.</strong> {failureText(order.failure?.reason)} The exchange will not report success or failure until the chain shows what happened.</p>
          <ul className="small" style={{ margin: "8px 0 0", paddingLeft: 18, listStyle: "disc" }}>
            <li>Do not try to buy again to &ldquo;fix&rdquo; it. Your units stay reserved, and new purchases from this wallet wait until it is resolved.</li>
            <li>The order is released automatically only after its transaction has expired with both wallets unchanged; otherwise an operator reconciles it against the chain.</li>
            <li>For the operator, quote order <code>{order.id}</code>.</li>
          </ul>
          <div className="actions-row" style={{ marginTop: 10 }}>
            <button className="button button-small button-outline" type="button" disabled={busy} onClick={() => refresh()}>Check status again</button>
            {txid && <Link className="link-button" href={`/verify?tx=${txid}`}>Check its transaction on the chain</Link>}
          </div>
        </div>}
        {order.failure && order.state !== "UNCERTAIN" && <p className="notice notice-bad" style={{ marginTop: 16 }}>
          {failureText(order.failure.reason)} {order.failure.transactionSubmitted === false && "Nothing was transferred."}
        </p>}
        {result === "NOT_VISIBLE" && <p className="notice notice-warn" style={{ marginTop: 16 }}>
          {order.settlementDropped
            ? "The node restarted before this settlement became durable and its block was dropped. The swap is no longer in the chain, so nothing changed hands; the order is held for operator review."
            : !order.chainActive ? "The experimental chain is unavailable, so the receipt cannot be checked right now. It is shown again once the node serves the same block."
            : "The settlement transaction is not visible in the current chain view, so its receipt is hidden. It reappears only if the node shows the same block again."}</p>}
        {sessionEnded && <p className="notice notice-warn" style={{ marginTop: 16 }}>Your demo session has ended, so this page can no longer read the order. If signing had already started, the order still finishes on the exchange; an order not yet signing is cancelled. Nothing is ever signed for an unconfirmed quote.</p>}
        {info && <p className="small muted" role="status" style={{ marginTop: 12 }}>{info}</p>}
        {error && <p className="inline-error" role="alert" style={{ marginTop: 12 }}>{error}</p>}
      </div>

      {order.receipt && <div className="panel">
        <div className="receipt-head"><p className="label">Receipt</p>{result === "VERIFIED" ? <span className="badge badge-ok">Verified on chain</span> : <span className="badge badge-blue">Durability pending</span>}</div>
        <dl className="facts">
          <div><dt>{buyer ? "Received" : "Delivered"}</dt><dd>{units} of {name}</dd></div>
          <div><dt>{buyer ? "Paid" : "Received"}</dt><dd>{zec(order.receipt.paymentZat)}</dd></div>
          {order.effects && <div><dt>Wallet change</dt><dd>{order.effects.assetUnits > 0 ? "+" : ""}{order.effects.assetUnits} units{order.effects.nativeZat !== null ? `, ${order.effects.nativeZat > 0 ? "+" : ""}${zec(order.effects.nativeZat)}` : ""} (from wallet rescans)</dd></div>}
          <div><dt>Transaction</dt><dd className="hash">{order.receipt.txid}</dd></div>
          <div><dt>Block</dt><dd>{order.receipt.height.toLocaleString("en-US")} · {order.receipt.confirmations.toLocaleString("en-US")} confirmation{order.receipt.confirmations === 1 ? "" : "s"}</dd></div>
          <div><dt>Network</dt><dd>Zcash regtest (experimental ZSA; disposable test chain)</dd></div>
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
        <ol className="progress" aria-label="Settlement progress">
          {stages.map(stage => <li key={stage.label} className={stage.status === "pending" ? "" : stage.status}
                                  aria-current={stage.status === "current" ? "step" : undefined}>
            {stage.label}<small>{stage.detail}</small></li>)}
        </ol>
        {failures > 1 && !sessionEnded && <p className="small muted stale" role="status">Live status paused: last update {staleFor}s ago. Retrying.</p>}
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
