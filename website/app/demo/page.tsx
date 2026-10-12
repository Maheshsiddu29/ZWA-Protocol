import type { Metadata } from "next";
import { VerifierDemo } from "@/components/demo/VerifierDemo";
import { CheckoutEvidence } from "@/components/demo/CheckoutEvidence";
export const metadata: Metadata = { title:"Live research demo", description:"Run real rooted-lineage verifier scenarios and inspect real local regtest checkout status and separately recorded evidence." };
export default function DemoPage() { return <><header className="subpage-hero"><div className="shell"><p className="eyebrow">ZWA / Demonstration</p><h1>Evidence you can inspect.</h1><p>Inspect a real local checkout session and an independent rooted-lineage verifier demonstration. Their evidence scopes are shown separately.</p></div></header><section id="verifier" className="subpage-section"><div className="shell"><VerifierDemo /></div></section><section id="checkout" className="subpage-section checkout-demo-section"><div className="shell"><CheckoutEvidence /></div></section></>; }
