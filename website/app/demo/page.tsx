import type { Metadata } from "next";
import { VerifierDemo } from "@/components/demo/VerifierDemo";
import { CheckoutEvidence } from "@/components/demo/CheckoutEvidence";
export const metadata: Metadata = { title:"Live research demo", description:"Run real rooted-lineage verifier scenarios and inspect read-only recorded regtest checkout evidence." };
export default function DemoPage() { return <><header className="subpage-hero"><div className="shell"><p className="eyebrow">ZWA / Demonstration</p><h1>Evidence you can inspect.</h1><p>The verifier and checkout are separate local research systems. This page shows their actual responses and makes unavailable states explicit.</p></div></header><section id="verifier" className="subpage-section"><div className="shell"><VerifierDemo /></div></section><section id="checkout" className="subpage-section checkout-demo-section"><div className="shell"><CheckoutEvidence /></div></section></>; }
