import { Protected } from "@/components/exchange/Protected";
import recorded from "../../../exchange/recorded-runs.json";

export const dynamic = "force-dynamic";

export default function AuditorPage() {
  const allowed = new Set((process.env.ZWA_AUDITOR_ALLOWED_TXIDS || "").split(",").filter(value => /^[0-9a-f]{64}$/.test(value)));
  const receipts = recorded.runs.filter(run => allowed.has(run.txid));
  return <Protected role="auditor" eyebrow="AUDITOR / SCOPED VIEW" title="Auditor access." intro="Auditor access is provisioned independently. Private investor credentials, viewing keys, and room metadata are never exposed by this portal.">
    {receipts.length ? receipts.map(run => <div className="exchange-order" key={run.txid}><span className="exchange-eyebrow">SCOPED HISTORICAL REGTEST RECEIPT</span><div><span>Instrument</span><strong>{run.instrumentId}</strong></div><div><span>Txid</span><code>{run.txid}</code></div><div><span>Quantity</span><strong>{run.quantity} units</strong></div><div><span>Buyer payment</span><strong>{run.paymentZat} zatoshis</strong></div><div><span>Observed depth at capture</span><strong>{run.confirmationsObservedBeforeShutdown}</strong></div><div><span>Current finality</span><strong>{run.currentFinality}</strong></div></div>) : <div className="exchange-empty"><h2>No scoped receipt has been assigned.</h2><p>The operator must explicitly authorize a txid before its recorded receipt can appear here. A public demo receipt is available in judge mode and is labeled as such.</p></div>}
  </Protected>;
}
