"use client";
import { useEffect, useState } from "react";

type Quote = { assetId: string; quantity: number; paymentAmount: number; networkFee: number; matcherFee: number; maximumTotalDebit: number; feePayer: string; quoteDigest: string; network: string; targetHeight: number };
type Receipt = { txid: string; height: number; blockHash: string; confirmations: number | null; finality: string; rootedLineage: string; investorEligibility: string; purchaseSettlement: string };
type Scan = { height: number; nativeZat: number; assetUnits: number };
type Scans = { buyerBefore: Scan; buyerAfter: Scan; sellerBefore: Scan; sellerAfter: Scan };
type Active = { sessionId: string; status: string | null; quote: Quote; receipt?: Receipt; walletScans?: Scans; operatorStages?: string[]; operatorStage?: string; approvalEvidence?: string; availability?: { state: string; tipHeight: number | null }; error?: { code: string } };
type Recorded = { quote: Quote; receipt: Receipt; status: string };
const stages = ["SESSION_PREPARED", "AWAITING_BUYER_APPROVAL", "BUYER_AUTHORIZED", "AWAITING_SELLER_APPROVAL", "SELLER_AUTHORIZED", "TRANSACTION_SUBMITTED", "TRANSACTION_INCLUDED", "CONFIRMATION_DEPTH_OBSERVED", "RECEIPT_AVAILABLE"];
const labels: Record<string,string> = { SESSION_PREPARED:"Session prepared", AWAITING_BUYER_APPROVAL:"Awaiting buyer approval", BUYER_AUTHORIZED:"Buyer authorized", AWAITING_SELLER_APPROVAL:"Awaiting seller approval", SELLER_AUTHORIZED:"Seller authorized", TRANSACTION_SUBMITTED:"Transaction submitted", TRANSACTION_INCLUDED:"Transaction included", CONFIRMATION_DEPTH_OBSERVED:"Confirmation depth observed", RECEIPT_AVAILABLE:"Receipt available" };
const fmt = (n: number | undefined) => n == null ? "—" : n.toLocaleString("en-US");

export function CheckoutEvidence() {
  const [active,setActive]=useState<Active | null>(null);
  const [recorded,setRecorded]=useState<Recorded | null>(null);
  const [state,setState]=useState("CHECKING");
  useEffect(()=>{
    let disposed=false;
    async function poll(){
      try {
        const r=await fetch("/api/checkout/active",{cache:"no-store"});
        const value=await r.json();
        if(disposed)return;
        if(r.ok && value?.quote && value?.sessionId){ setActive(value); setState("LIVE"); }
        else { setActive(null); setState(r.status===404 && value?.error?.code==="NO_ACTIVE_SESSION" ? "NO_ACTIVE_SESSION" : String(value?.error?.code || "BACKEND_UNAVAILABLE")); }
      } catch { if(!disposed){setActive(null);setState("BACKEND_UNAVAILABLE");} }
    }
    void poll();
    const timer=window.setInterval(poll,2500);
    return ()=>{disposed=true;window.clearInterval(timer);};
  },[]);
  useEffect(()=>{fetch("/api/checkout/session",{cache:"no-store"}).then(r=>r.ok?r.json():null).then(value=>{if(value?.status==="RECORDED_REGTEST_EVIDENCE")setRecorded(value);}).catch(()=>{});},[]);
  const quote=active?.quote;
  const receipt=active?.receipt;
  const observed=active?.operatorStages || [];
  const failure=active?.operatorStage?.endsWith("NOT_GRANTED") || ["APPROVAL_TIMEOUT","SIGNER_UNAVAILABLE","EXECUTION_FAILED","EXECUTION_UNCERTAIN"].includes(active?.operatorStage || "") || ["FAILED","EXPIRED_LOCALLY","INVALIDATED_BY_REORG"].includes(active?.status || "");
  const stage=active?.operatorStage || (active?.status==="QUOTE_READY" ? "SESSION_PREPARED" : "");
  return <div className="checkout-live" aria-live="polite">
    <div className="checkout-live-head"><div><p className="eyebrow">One asset · disposable regtest</p><h2>Follow a real checkout.</h2><p className="demo-muted">The operator starts a fresh local session. Buyer and seller approve directly in their independent wallet terminals. This page observes public evidence.</p></div><span className="read-only-badge">{active ? "LIVE LOCAL SESSION" : "NO LIVE PURCHASE"}</span></div>
    {!active && <div className="checkout-empty"><img src="/illustrations/asset-tokens-onchain.svg" alt="" /><div><h3>{state==="CHECKING"?"Checking for an active session…":state==="NO_ACTIVE_SESSION"?"Ready for an operator-run session":"Live checkout unavailable"}</h3><p>{state==="NO_ACTIVE_SESSION"?"Start the Phase 4B launcher in a local terminal. A fresh asset, quote and approval stages appear here when observed.":state==="CHECKING"?"The local read-only service is being checked.":`The local service returned ${state}. No live settlement is being claimed.`}</p></div></div>}
    {active && <>
      <div className="checkout-progress" aria-label="Checkout stages">{["Asset","Verification","Quote","Approval","Settlement","Receipt"].map((label,i)=><span key={label} className={i < (receipt?6:observed.includes("BUYER_AUTHORIZED")?4:quote?3:1)?"reached":""}>{String(i+1).padStart(2,"0")} {label}</span>)}</div>
      <div className="checkout-product-grid"><section className="checkout-asset"><p className="eyebrow">01 / Asset</p><img src="/illustrations/asset-tokens-onchain.svg" alt="" /><h3>Experimental custom ZSA</h3><p>Freshly issued in the disposable local regtest run.</p><div className="checkout-data"><span>Canonical AssetBase</span><strong className="hash-value">{quote?.assetId}</strong></div><div className="checkout-data"><span>Offered quantity</span><strong>{fmt(quote?.quantity)} units</strong></div></section>
      <section className="checkout-verification"><p className="eyebrow">02 / Verification</p><h3>Evidence scope</h3><div className="verification-note"><strong>Checkout asset lineage: unsupported</strong><p>The historical rooted-lineage fixture above verifies a separate research view. It does not prove provenance of this freshly issued asset or its purchase output.</p></div><dl className="checkout-facts"><div><dt>Asset identity</dt><dd>Bound by the exact public quote digest</dd></div><div><dt>Issuance root authentication</dt><dd>Not established for this run</dd></div><div><dt>Purchased note lineage</dt><dd>Unsupported</dd></div><div><dt>Trade eligibility</dt><dd>Not evaluated</dd></div><div><dt>Chain</dt><dd>{quote?.network} · experimental SWAP branch</dd></div></dl></section></div>
      <section className="checkout-quote"><p className="eyebrow">03 / Exact quote</p><div className="checkout-quote-grid"><div><span>Asset quantity</span><strong>{fmt(quote?.quantity)} ZSA</strong></div><div><span>Buyer pays</span><strong>{fmt(quote?.paymentAmount)} zatoshis</strong></div><div><span>Sponsor network fee</span><strong>{fmt(quote?.networkFee)} zatoshis</strong></div><div><span>Matcher fee</span><strong>{fmt(quote?.matcherFee)} zatoshis</strong></div><div className="total"><span>Total buyer debit</span><strong>{fmt(quote?.maximumTotalDebit)} zatoshis</strong></div></div><details><summary>Exact terms and expiry</summary><p>Quote digest <code>{quote?.quoteDigest}</code></p><p>Target block {quote?.targetHeight}; sponsor pays the network fee. This digest is an off-chain approval token.</p></details></section>
      <div className="checkout-product-grid"><section className="checkout-approval"><p className="eyebrow">04 / Wallet approval</p><h3>{failure?"Approval or execution stopped":stage==="AWAITING_BUYER_APPROVAL"?"Awaiting buyer approval":stage==="AWAITING_SELLER_APPROVAL"?"Awaiting seller approval":"Independent wallet decisions"}</h3><p>Approve in your local wallet terminal. Each signer reviews the exact asset, quantity, payment, recipients, sponsor fee and digest. An empty response rejects.</p><strong className="checkout-stage">{(stage || active.status || "UNKNOWN").replaceAll("_"," ")}</strong><small>Operator event observations are local status, not a cryptographic attestation of who typed approval.</small></section><section className="checkout-settlement"><p className="eyebrow">05 / Settlement</p><h3>Observed lifecycle</h3><ol>{stages.map(item=><li key={item} className={observed.includes(item)?"done":""}><span>{labels[item]}</span><strong>{observed.includes(item)?"Observed":"Pending"}</strong></li>)}</ol>{failure && <p className="service-state">{active.operatorStage || active.status}. No successful trade authorization is claimed.</p>}{active.status===null && <p className="service-state">{active.error?.code || active.availability?.state || "NODE_UNAVAILABLE"}: live chain state cannot be confirmed.</p>}</section></div>
      <section className="checkout-receipt"><p className="eyebrow">06 / Receipt</p>{receipt && active.status!==null ? <><h3>{active.status==="FINALIZED"?"Depth 3 observed":"Transaction evidence observed"}</h3><div className="checkout-live-grid"><div><span>Transaction ID</span><strong className="hash-value">{receipt.txid}</strong></div><div><span>Block height / hash</span><strong>{receipt.height} <small className="hash-value">{receipt.blockHash}</small></strong></div><div><span>Observed confirmations</span><strong>{receipt.confirmations ?? "Unknown"}</strong></div><div><span>Finality qualification</span><strong>{receipt.finality.replaceAll("_"," ")}</strong></div><div><span>Buyer asset effect · wallet scan</span><strong>{active.walletScans ? `${fmt(active.walletScans.buyerBefore.assetUnits)} → ${fmt(active.walletScans.buyerAfter.assetUnits)} ZSA` : "Wallet scan pending publication"}</strong></div><div><span>Seller payment effect · wallet scan</span><strong>{active.walletScans ? `${fmt(active.walletScans.sellerBefore.nativeZat)} → ${fmt(active.walletScans.sellerAfter.nativeZat)} zatoshis` : "Wallet scan pending publication"}</strong></div><div><span>Sponsor fee in verified terms</span><strong>{fmt(quote?.networkFee)} zatoshis</strong></div><div><span>Rooted lineage</span><strong>{receipt.rootedLineage}</strong></div></div></>:<p>No verified live receipt is available. Settlement and wallet effects are shown only after evidence is observed.</p>}</section>
    </>}
    <details className="checkout-history"><summary>Inspect separate historical checkout evidence</summary>{recorded?<p>Recorded Phase 3.5H human-run transaction: <code>{recorded.receipt.txid}</code>. It is historical and its current finality is {recorded.receipt.finality}.</p>:<p>Historical evidence service unavailable.</p>}</details>
    <p className="checkout-disclaimer">Experimental local regtest only. The purchase is not cryptographically gated by the independent rooted-lineage fixture. Three confirmations satisfy a local display policy, not irreversible consensus finality.</p>
  </div>;
}
