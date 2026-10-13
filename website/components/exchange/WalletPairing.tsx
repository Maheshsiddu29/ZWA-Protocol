"use client";

import { useEffect, useState } from "react";
import { postJson } from "@/components/exchange/client-api";

type Status = { paired: boolean; role: "buyer" | "seller"; approvalMode: string; walletAddress?: string;
  expiresAt?: number; companionOnline?: boolean };

export function WalletPairing({ initial, csrf }: { initial: Status; csrf: string }) {
  const [status, setStatus] = useState(initial);
  const [code, setCode] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const timer = setInterval(async () => {
      const response = await fetch("/api/exchange/wallet", { cache: "no-store" });
      if (response.ok) setStatus(await response.json());
    }, 3000);
    return () => clearInterval(timer);
  }, []);

  async function pair(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true); setMessage("");
    const result = await postJson("/api/exchange/wallet", csrf, { code });
    setMessage(result.ok ? "Code accepted. Type PAIR in your wallet companion terminal to finish pairing."
                         : result.data?.error ?? "Pairing failed.");
    if (result.ok) setCode("");
    setBusy(false);
  }

  async function unpair() {
    setBusy(true);
    const result = await postJson("/api/exchange/wallet/unpair", csrf);
    if (result.ok) setStatus(result.data);
    setBusy(false);
  }

  const human = status.approvalMode === "HUMAN_APPROVED";
  return <div className="split">
    <div className="panel">
      <p className="label">{status.role} wallet</p>
      {status.paired ? <>
        <h2>Wallet paired</h2>
        <dl className="facts">
          <div><dt>Receiving address</dt><dd className="hash">{status.walletAddress}</dd></div>
          <div><dt>Companion</dt><dd>{status.companionOnline ? "Connected" : "Not connected. Start it to approve trades."}</dd></div>
          <div><dt>Pairing ends</dt><dd>{status.expiresAt ? new Date(status.expiresAt * 1000).toLocaleTimeString() : "—"}</dd></div>
          <div><dt>Approval</dt><dd>{human ? "Typed by you in the wallet terminal" : "Scripted test signer (not human approval)"}</dd></div>
        </dl>
        <button className="link-button quiet" type="button" disabled={busy} onClick={unpair}>Unpair this wallet</button>
      </> : <>
        <h2>Pair your wallet</h2>
        <ol className="small" style={{ paddingLeft: 18, lineHeight: 1.8 }}>
          <li>In your own terminal, start the ZWA wallet companion for your {status.role} wallet.</li>
          <li>Enter the one-time code it shows.</li>
          <li>Confirm this account in the companion by typing <code>PAIR</code>.</li>
        </ol>
        <form onSubmit={pair}>
          <label className="field-label" htmlFor="pairing-code">Pairing code</label>
          <div className="form-row" style={{ marginTop: 6 }}>
            <input className="field" id="pairing-code" value={code} onChange={e => setCode(e.target.value)} placeholder="ABCDE-FGHIJ"
                   autoComplete="off" maxLength={16} required style={{ flex: "1 1 200px" }} />
            <button className="button button-blue button-small" type="submit" disabled={busy}>Pair wallet</button>
          </div>
        </form>
      </>}
      {message && <p className="small" role="status" style={{ marginTop: 10 }}>{message}</p>}
    </div>
    <aside className="panel panel-muted">
      <p className="label">What pairing means</p>
      <p className="small">Pairing links this signed-in account to one local wallet companion for a few hours. It shows that the person using this browser can also see and type into that wallet&apos;s terminal.</p>
      <p className="small">It is not a wallet login and never gives this website a key. Every trade is reviewed and approved inside the wallet itself, where the default answer is reject.</p>
    </aside>
  </div>;
}
