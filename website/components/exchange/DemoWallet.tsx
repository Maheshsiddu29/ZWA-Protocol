"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { postJson } from "@/components/exchange/client-api";
import { assetName, shortHash, zec } from "@/lib/exchange";
import type { WalletStatus } from "@/lib/private-orders";

const steps = [["CREATED", "Wallet created"], ["FUNDING", "Funded with test ZEC"], ["READY", "Ready to trade"]] as const;
const order = ["REQUESTED", "CREATED", "FUNDING", "READY"];

export function DemoWallet({ initial, csrf }: { initial: WalletStatus | null; csrf: string }) {
  const router = useRouter();
  const [status, setStatus] = useState<WalletStatus | null>(initial);
  const [ended, setEnded] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [retired, setRetired] = useState(false);
  const [error, setError] = useState("");
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (ended) return;
    const poll = setInterval(async () => {
      const response = await fetch("/api/exchange/wallet", { cache: "no-store" });
      const body = await response.json().catch(() => null);
      if (response.ok) setStatus(body);
      else if (response.status === 409) setEnded(body?.error === "demo wallet could not be created; start a new demo"
        ? "The wallet could not be created. Please start a new demo." : "Your demo session has ended.");
    }, status?.state === "READY" ? 10000 : 1500);
    const clock = setInterval(() => setNow(Date.now()), 1000);
    return () => { clearInterval(poll); clearInterval(clock); };
  }, [ended, status?.state]);

  async function end() {
    setBusy(true); setError("");
    const result = await postJson("/api/demo/end", csrf);
    if (result.ok) { router.push("/"); router.refresh(); }
    else { setError(result.data?.error ?? "The session could not be ended."); setBusy(false); }
  }

  async function restart() {
    setBusy(true); setError("");
    if (!retired) {
      const result = await postJson("/api/demo/end", csrf);
      if (!result.ok) { setError(result.data?.error ?? "The old session could not be ended."); setBusy(false); return; }
      setRetired(true);
    }
    try {
      const response = await fetch("/api/demo/session", { method: "POST", credentials: "same-origin",
        headers: { "content-type": "application/json" }, body: "{}" });
      const body = await response.json().catch(() => ({}));
      if (response.ok && body.next === "/wallet") { router.push("/wallet"); router.refresh(); return; }
      setError(body.error ?? "A new demo could not be started.");
    } catch { setError("The demo service is unreachable."); }
    setBusy(false);
  }

  if (ended) return <div className="empty"><h2>{ended}</h2>
    <p>Start a new session to get a fresh wallet.</p>
    <button className="button button-blue" type="button" onClick={restart} disabled={busy}>
      {busy ? "Starting…" : "Start a new demo"}</button>
    {error && <p className="inline-error" role="alert">{error}</p>}</div>;
  if (!status) return <div className="empty"><h2>Wallet service unavailable</h2>
    <p>Your session is preserved while the service recovers.</p>
    <button className="button button-outline" type="button" onClick={() => router.refresh()}>Retry</button></div>;
  const position = order.indexOf(status.state ?? "REQUESTED");
  const ready = status.state === "READY";
  const remaining = status.expiresAt ? Math.max(0, status.expiresAt - Math.floor(now / 1000)) : null;
  const holdings = (status.holdings ?? []).filter(h => h.units > 0);
  return <div className="split">
    <div className="stack">
      <div className="panel">
        <div className="receipt-head"><p className="label">Demo wallet</p>
          {ready ? <span className="badge badge-ok"><span className="status-dot" />Ready</span> : <span className="badge badge-blue">Setting up</span>}</div>
        {!ready && <>
          <h2>Preparing your wallet</h2>
          <div className="wallet-steps">{steps.map(([state, label], index) => {
            const done = position >= index + 1;
            const current = !done && position === index;
            return <div key={state} className={done ? "done" : current ? "current" : ""}>{label}</div>;
          })}</div>
          <p className="small muted">The exchange creates a new key, funds it with test ZEC in a block of its own, then scans the chain to confirm the balance. This usually takes under ten seconds; if another purchase is settling it waits for that block first.</p>
        </>}
        {ready && <>
          <div className="figure" style={{ margin: "12px 0 6px" }}><strong>{zec(status.nativeZat)}</strong><span>test ZEC</span></div>
          <p className="small muted" style={{ marginTop: 0 }}>Scanned at block {status.scanHeight?.toLocaleString("en-US")} from the wallet&apos;s own keys.</p>
          <div className="actions-row"><Link className="button button-blue" href="/exchange">Browse assets</Link>
            {status.openOrderId && <Link className="link-button" href={`/orders/${status.openOrderId}`}>Order in progress →</Link>}</div>
        </>}
      </div>
      {ready && <div className="panel">
        <p className="label">Holdings</p>
        {holdings.length ? <div className="list" style={{ marginTop: 12 }}>{holdings.map(h => <div className="list-row" key={h.assetBase}>
          <span><strong>{assetName(h.instrumentId)}</strong><small>Scanned at block {h.scanHeight.toLocaleString("en-US")}</small></span>
          <span className="hide-sm" /><span className="hide-sm" /><span className="num">{h.units} units</span></div>)}</div>
          : <p className="muted small">No assets yet. Purchases appear here after the wallet scan confirms them.</p>}
      </div>}
    </div>
    <aside className="stack">
      <div className="panel panel-muted">
        <p className="label">Session</p>
        <dl className="facts">
          <div><dt>Address</dt><dd className="hash">{status.walletAddress ?? "Creating…"}</dd></div>
          {status.fundingTxid && <div><dt>Funding transaction</dt><dd className="hash">{shortHash(status.fundingTxid, 14, 8)}</dd></div>}
          <div><dt>Purchases</dt><dd>{status.ordersUsed ?? 0} of {status.limits?.maxOrders ?? "—"} used · up to {status.limits?.maxUnitsPerOrder} units each</dd></div>
          <div><dt>Session ends</dt><dd className="countdown">{remaining !== null ? `in ${Math.floor(remaining / 3600)}h ${String(Math.floor(remaining % 3600 / 60)).padStart(2, "0")}m` : "—"}</dd></div>
        </dl>
        <p className="small muted">Custodial demo: this server holds the key. It signs only purchases you confirm, within the limits above, on the test network.</p>
        <button className="link-button quiet" type="button" disabled={busy} onClick={end}>End session and delete wallet</button>
        {error && <p className="inline-error" role="alert">{error}</p>}
      </div>
    </aside>
  </div>;
}
