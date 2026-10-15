import type { Metadata } from "next";
import { demoMode } from "@/lib/exchange-auth";
import { verifyDemoTx } from "@/lib/private-orders";
import { remote } from "@/lib/remote-exchange";
import { kindLabel, networkPins, publicReceipts } from "@/lib/public-testnet";

export const metadata: Metadata = { title: "Verify" };
export const dynamic = "force-dynamic";

const day = (unix: number) => new Date(unix * 1000).toISOString().slice(0, 10);

export default async function VerifyPage({ searchParams }: { searchParams: Promise<{ tx?: string }> }) {
  const { tx } = await searchParams;
  const txid = typeof tx === "string" ? tx.trim().toLowerCase() : "";
  const valid = /^[0-9a-f]{64}$/.test(txid);
  const check = valid ? await verifyDemoTx(txid) : null;
  // Remote mode: the chain lookup has no JSON API on the exchange, so it is offered on the exchange's own page.
  const config = remote();
  const exchange = config.state === "on" ? config.exchange : null;
  return <section className="page"><div className="shell">
    <header className="page-head"><div><p className="eyebrow">Verify</p><h1>Check a transaction</h1>
      <p className="lede">Look up whether a transaction is included in the exchange&apos;s demo chain. Only public chain facts are shown: no amounts, parties or order details.</p></div></header>
    <div className="split">
      <div className="stack">
        <form className="panel" action="/verify" method="get">
          <label className="field-label" htmlFor="tx">Transaction ID</label>
          <div className="form-row" style={{ marginTop: 6 }}>
            <input className="field" id="tx" name="tx" defaultValue={txid} placeholder="64 hexadecimal characters" maxLength={64} style={{ flex: "1 1 320px" }} />
            <button className="button button-blue button-small" type="submit" disabled={!demoMode()}>Check</button>
          </div>
          {!demoMode() && <p className="small muted" style={{ marginTop: 10 }}>The demo chain is not served by this deployment.</p>}
        </form>
        {txid && !valid && <p className="notice notice-bad">A transaction ID is 64 hexadecimal characters.</p>}
        {valid && check && <div className="panel">
          {check.chainActive === false ? <p className="notice notice-warn">The demo chain is unavailable right now.</p>
            : check.included ? <>
              <span className="badge badge-ok">Included</span>
              <dl className="facts" style={{ marginTop: 12 }}>
                <div><dt>Transaction</dt><dd className="hash">{txid}</dd></div>
                <div><dt>Block</dt><dd>{check.height?.toLocaleString("en-US")} · {check.confirmations?.toLocaleString("en-US")} confirmations</dd></div>
                <div><dt>Block hash</dt><dd className="hash">{check.blockHash}</dd></div>
                <div><dt>Size</dt><dd>{check.sizeBytes?.toLocaleString("en-US")} bytes</dd></div>
                <div><dt>Chain</dt><dd>Demo regtest chain, genesis <span className="hash">{check.genesisHash?.slice(0, 16)}…</span></dd></div>
              </dl>
              <p className="small muted">Confirmations count blocks on a private test chain operated by this exchange. They are not finality on any public network.</p>
            </> : <p className="notice notice-warn">{check.found ? "This transaction is known to the node but is not in a block." : "This transaction is not in the demo chain."}</p>}
        </div>}
        {valid && exchange && <div className="panel">
          <p>This lookup is served by the exchange host, which runs the demo chain.</p>
          <p style={{ marginTop: 12 }}><a className="link-button" href={`${exchange}/verify?tx=${txid}`} rel="noopener noreferrer">Check this transaction on the exchange host ↗</a></p>
          <p className="small muted">Your order page already shows the receipt, re-checked against the live chain by the exchange.</p>
        </div>}
        {valid && !check && !exchange && demoMode() && <p className="notice notice-warn">The verification service is unavailable.</p>}
      </div>
      <aside className="panel panel-muted">
        <p className="label">Public testnet records</p>
        <p className="small muted">ZWA also issued and transferred a ZSA on the public {networkPins.label}. That network does not support the exchange&apos;s atomic swap, so these are separate from the demo.</p>
        <div className="stack" style={{ marginTop: 12 }}>{publicReceipts.map(receipt => <div key={receipt.transaction.txid} className="panel" style={{ padding: 18 }}>
          <strong>{kindLabel[receipt.transaction.kind]}</strong>
          <dl className="facts" style={{ marginTop: 6 }}>
            <div><dt>Transaction</dt><dd className="hash">{receipt.transaction.txid}</dd></div>
            <div><dt>Block</dt><dd>{receipt.inclusion.blockHeight?.toLocaleString("en-US")} · {day(receipt.inclusion.observedAtUnix)}</dd></div>
            <div><dt>Checked via</dt><dd>{receipt.network.lightwalletd}</dd></div>
          </dl>
        </div>)}</div>
        <p className="small muted" style={{ marginTop: 12 }}>Inclusion was checked against the operator&apos;s public lightwalletd when recorded. Shielded amounts and recipients are not public.</p>
      </aside>
    </div>
  </div></section>;
}
