import type { Metadata } from "next";

export const metadata: Metadata = { title: "Terms" };

export default function TermsPage() {
  return <section className="page"><div className="shell">
    <header className="page-head"><div><p className="eyebrow">Legal</p><h1>Terms</h1></div></header>
    <div className="legal-copy">
      <p>ZWA Exchange is experimental software for research and demonstration. It runs on a disposable Zcash regtest chain that the operator can reset at any time.</p>
      <h2>No value, no rights</h2>
      <p>The demo assets and test ZEC have no monetary value. They are not backed by gold, government debt, credit or any other asset, and they cannot be redeemed. Nothing here is an offer of securities or investment advice.</p>
      <h2>Custodial demo wallets</h2>
      <p>A demo wallet is created and held by this server for your session. When you confirm a quote, its restricted signer signs only that exact purchase, within published limits, on the test network. This is not the same as signing with a wallet you control.</p>
      <h2>No warranty</h2>
      <p>The software is provided as is. Experimental Zcash Shielded Asset features are not production Zcash mainnet functionality, and Zcash consensus does not enforce the exchange&apos;s eligibility rules for transfers made outside it.</p>
    </div>
  </div></section>;
}
