import type { Metadata } from "next";

export const metadata: Metadata = { title: "Privacy" };

export default function PrivacyPage() {
  return <section className="page"><div className="shell">
    <header className="page-head"><div><p className="eyebrow">Legal</p><h1>Privacy</h1></div></header>
    <div className="legal-copy">
      <p>This site has no analytics, advertising or third-party tracking.</p>
      <h2>Demo sessions</h2>
      <p>Starting a demo sets one signed, HTTP-only session cookie. The server stores a hash of the session identifier, the disposable wallet it created for you, your orders and their receipts on the experimental chain. To limit abuse, it also stores a keyed hash of your network address for one hour; the address itself is not stored.</p>
      <p>Ending a session, or letting it expire, deletes the wallet&apos;s keys. Order records and receipts are kept so the exchange&apos;s ledger stays consistent.</p>
      <h2>What not to enter</h2>
      <p>Never enter a real seed phrase, private key, password or personal information anywhere on this site. The demo never asks for them.</p>
    </div>
  </div></section>;
}
