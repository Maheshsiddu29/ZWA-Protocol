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
  return <div className="exchange-wallet">
    <section className="exchange-panel">
      <span className="exchange-eyebrow">{status.role.toUpperCase()} WALLET</span>
      {status.paired ? <>
        <h2>Wallet paired</h2>
        <div className="exchange-facts">
          <div><span>Receiving address</span><strong className="exchange-hash">{status.walletAddress}</strong></div>
          <div><span>Companion</span><strong>{status.companionOnline ? "Connected" : "Not connected — start it to approve trades"}</strong></div>
          <div><span>Pairing ends</span><strong>{status.expiresAt ? new Date(status.expiresAt * 1000).toLocaleTimeString() : "—"}</strong></div>
          <div><span>Approval mode</span><strong>{human ? "Human approval in your wallet terminal" : "Scripted test mode (not human approval)"}</strong></div>
        </div>
        <button className="exchange-link-button" type="button" disabled={busy} onClick={unpair}>Unpair this wallet</button>
      </> : <>
        <h2>Pair your local wallet</h2>
        <ol className="exchange-numbered">
          <li>In your own terminal, start the ZWA wallet companion for your {status.role} wallet.</li>
          <li>Enter the one-time code it shows.</li>
          <li>Confirm this account in the companion by typing <code>PAIR</code>.</li>
        </ol>
        <form onSubmit={pair} className="exchange-inline-form">
          <label htmlFor="pairing-code">Pairing code</label>
          <input id="pairing-code" value={code} onChange={e => setCode(e.target.value)} placeholder="ABCDE-FGHIJ"
                 autoComplete="off" maxLength={16} required />
          <button className="exchange-button" type="submit" disabled={busy}>Pair wallet <span>↗</span></button>
        </form>
      </>}
      {message && <p role="status">{message}</p>}
    </section>
    <aside className="exchange-panel">
      <span className="exchange-eyebrow">WHAT PAIRING MEANS</span>
      <p>Pairing links this signed-in account to one local wallet companion for a few hours. It shows that the person using this browser can also see and type into that wallet&apos;s terminal.</p>
      <p>It is not a wallet login and it never gives this website a key. Every trade is still reviewed and approved inside the wallet itself, where the default answer is reject.</p>
    </aside>
  </div>;
}
