import Link from "next/link";
import { ExchangeShell } from "@/components/exchange/ExchangeShell";
import { assets } from "@/lib/exchange";
import recorded from "../../../../exchange/recorded-runs.json";

const steps = [
  "Browse three distinct experimental asset definitions and issuance identities.",
  "Open the gold-reference asset and inspect its rights and pricing source.",
  "Check the XAU/USD reference when a fresh Pyth API response is configured.",
  "Review issuer, transfer, redemption, and eligibility disclosures.",
  "Prepare a native ZEC quote in the local operator wallet session.",
  "Verify the combined Groth16 issuance and eligibility authorization.",
  "Review exact buyer and seller wallet effects before each signer authorizes.",
  "Submit the real experimental ZSA-for-ZEC transaction.",
  "Reconcile the buyer asset scan and seller ZEC credit.",
  "Inspect observed inclusion depth and the public receipt.",
  "Sign in as an auditor with an explicitly allowed txid scope.",
  "Mutate an AssetBase, stale price, or permission and observe rejection.",
];

export default function JudgePage() {
  return <ExchangeShell eyebrow="JUDGE / EVIDENCE" title="From listing to wallet." intro="A guided path through the local exchange. The records below are completed scripted regtest tests from separate disposable chains; they are historical evidence, never a new authorization.">
    <div className="exchange-section-heading"><div><span className="exchange-eyebrow">DEMO SEQUENCE</span><h2>Twelve checks, one product story.</h2></div><Link href="/demo#checkout">Open live checkout ↗</Link></div>
    <ol className="exchange-judge-steps">{steps.map((step,index)=><li key={step}><span>{String(index+1).padStart(2,"0")}</span><p>{step}</p></li>)}</ol>
    <div className="exchange-section-heading"><div><span className="exchange-eyebrow">RECORDED TEST EVIDENCE</span><h2>Three completed asset classes.</h2></div></div>
    <div className="exchange-judge-runs">{recorded.runs.map(run => {const asset=assets.find(item=>item.id===run.instrumentId);return <article className="exchange-order" key={run.txid}><span className="exchange-eyebrow">{asset?.category.replaceAll("_"," ")} · SCRIPTED TEST</span><h3>{asset?.name}</h3><div><span>AssetBase</span><code>{run.assetBase}</code></div><div><span>Purchase</span><strong>{run.quantity} units for {run.paymentZat.toLocaleString("en-US")} zatoshis</strong></div><div><span>Transaction</span><code>{run.txid}</code></div><div><span>Buyer scan</span><strong>{run.buyerAssetBefore} → {run.buyerAssetAfter} units</strong></div><div><span>Seller scan</span><strong>{run.sellerAssetBefore} → {run.sellerAssetAfter} units; {run.sellerNativeBeforeZat} → {run.sellerNativeAfterZat} zatoshis</strong></div><div><span>Receipt</span><strong>{run.confirmationsObservedBeforeShutdown} confirmations observed before node shutdown · current finality unknown</strong></div></article>})}</div>
    <p className="exchange-caveat">Recorded runs used scripted test signers. Human approval remains a separate interactive terminal workflow. The market reference, NAV adapter, and private quote adapter are distinct; no USD reference was converted into these ZEC payments. The live chain is required for fresh receipt claims.</p>
  </ExchangeShell>;
}
