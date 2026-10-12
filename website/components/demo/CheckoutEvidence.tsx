"use client";
import { useEffect, useState } from "react";

type Data = Record<string, unknown>;
type FetchState = { value?: Data; error?: string };
const names = ["info","quote","session","receipt"] as const;
export function CheckoutEvidence() {
  const [data,setData]=useState<Record<string,FetchState>>({});
  useEffect(()=>{ Promise.all(names.map(async name=>{ try { const r=await fetch(`/api/checkout/${name}`,{cache:"no-store"}); const value=await r.json(); return [name,r.ok?{value}:{error:String(value?.error?.code || r.status)}] as const; } catch { return [name,{error:"BACKEND_UNAVAILABLE"}] as const; } })).then(rows=>setData(Object.fromEntries(rows))); },[]);
  const info=data.info?.value;
  const quote=(data.quote?.value?.quote || {}) as Data;
  const session=data.session?.value;
  const receipt=data.receipt?.value;
  const unavailable=Object.values(data).some(item=>item.error);
  return <div className="checkout-live"><div className="checkout-live-head"><div><p className="eyebrow">Read-only local evidence</p><h2>Recorded-phase35h</h2></div><span className="read-only-badge">No purchase action</span></div>
    {Object.keys(data).length===0 && <p>Loading local checkout endpoints…</p>}
    {unavailable && <p className="service-state">One or more local checkout endpoints are unavailable. This panel does not infer live status from historical evidence.</p>}
    <div className="checkout-live-grid"><div><span>Network / availability</span><strong>{String(info?.network || "—")} / {String((info?.availability as Data)?.state || "Unavailable")}</strong></div><div><span>Session status</span><strong>{String(session?.status || "Unavailable")}</strong></div><div><span>Quantity / buyer debit</span><strong>{quote.quantity != null ? `${quote.quantity} ZSA / ${quote.paymentAmount} zatoshis` : "Unavailable"}</strong></div><div><span>Sponsored network fee</span><strong>{quote.networkFee != null ? `${quote.networkFee} zatoshis` : "Unavailable"}</strong></div><div><span>Current finality</span><strong>{String(receipt?.finality || "Unavailable")}</strong></div><div><span>Purchased-note lineage</span><strong>{String(receipt?.rootedLineage || "Unavailable")}</strong></div><div><span>Investor eligibility</span><strong>{String(receipt?.investorEligibility || "Unavailable")}</strong></div><div><span>Transaction ID</span><strong className="hash-value">{String(receipt?.txid || "Unavailable")}</strong></div></div>
    <p className="checkout-disclaimer">The session and receipt endpoints describe a past disposable regtest run. Current node availability is separate from historical receipt evidence; when the node is stopped, current finality is UNKNOWN.</p>
  </div>;
}
