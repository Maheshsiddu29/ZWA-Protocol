import type { Metadata } from "next";
import { VerifierDemo } from "@/components/demo/VerifierDemo";
import { CheckoutEvidence } from "@/components/demo/CheckoutEvidence";

export const metadata: Metadata = {
  title: "Live research demo",
  description: "Inspect a local regtest checkout and a separate rooted-lineage verifier demonstration.",
};

export default function DemoPage() {
  return <>
    <header className="subpage-hero"><div className="shell">
      <p className="eyebrow">ZWA / Demonstration</p>
      <h1>Evidence you can inspect.</h1>
      <p>Follow one experimental ZSA purchase from asset to receipt. Inspect a separate rooted-lineage research proof below.</p>
    </div></header>
    <section id="checkout" className="subpage-section checkout-demo-section"><div className="shell"><CheckoutEvidence /></div></section>
    <section id="verifier" className="subpage-section"><div className="shell"><div className="demo-research-intro"><p className="eyebrow">Independent verifier demonstration</p><h2>A separate research proof.</h2><p>This pinned fixture does not authorize the checkout asset or transaction above.</p></div><VerifierDemo /></div></section>
  </>;
}
