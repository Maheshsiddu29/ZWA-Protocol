import Image from "next/image";
import Link from "next/link";
import { OfficialMotion } from "@/components/motion/OfficialMotion";
import { MarketTable } from "@/components/exchange/MarketTable";
import { TryDemoButton } from "@/components/exchange/TryDemoButton";
import { currentSession, demoMode } from "@/lib/exchange-auth";
import { market } from "@/lib/private-orders";

export const dynamic = "force-dynamic";

const steps = [
  ["Get a demo wallet", "One click creates a disposable shielded wallet for this browser session and funds it with test ZEC."],
  ["Choose an asset", "Browse experimental gold, treasury and credit instruments with live dealer prices and verified inventory."],
  ["Confirm the exact quote", "The server prices your order to the zatoshi. You confirm those exact terms before anything is signed."],
  ["Settle and hold", "A zero-knowledge eligibility proof is checked, both wallets sign, and the swap settles in one transaction."],
];

const proofs = [
  { name: "Provenance", src: "/illustrations/ring-provenance.svg", status: "Checked on every order",
    text: "Each order's Groth16 proof shows the asset was issued under an authenticated issuer root. It does not trace an individual note's history." },
  { name: "Eligibility", src: "/illustrations/ring-eligibility.svg", status: "Checked on every order",
    text: "The same proof shows the buyer holds a valid credential, without revealing the credential itself." },
  { name: "Settlement", src: "/illustrations/ring-settlement.svg", status: "Atomic ZSA-for-ZEC swap",
    text: "Asset and payment move in one shielded transaction. Balances are reconciled from independent wallet scans." },
];

export default async function Home() {
  const [listing, session] = await Promise.all([market(), currentSession()]);
  const demo = demoMode();
  const cta = session?.kind === "demo" ? <Link className="button button-blue" href="/exchange">Open the exchange <span aria-hidden="true">↗</span></Link>
    : demo ? <TryDemoButton /> : <Link className="button button-blue" href="/exchange">Open the exchange <span aria-hidden="true">↗</span></Link>;
  return <>
    <section className="hero" aria-labelledby="hero-title">
      <div className="hero-backdrop" aria-hidden="true" />
      <div className="hero-content shell">
        <div className="hero-copy"><p className="eyebrow eyebrow-light">ZWA Exchange · Experimental</p>
          <h1 id="hero-title">Private assets.<br /><span>Verifiable origins.</span></h1>
          <p className="hero-lede">Buy tokenized real-world assets with shielded settlement on Zcash. Every order carries a zero-knowledge proof of provenance and eligibility, and settles as a single atomic swap.</p>
          <div className="hero-actions">{cta}<Link className="text-link text-link-light" href="/exchange">View markets</Link></div>
          <p className="hero-note">Runs on a disposable Zcash regtest chain · Test assets with no value</p>
        </div>
      </div>
    </section>

    <section className="home-steps" aria-labelledby="steps-title"><div className="shell">
      <div className="home-head"><div><p className="eyebrow">How it works</p><h2 id="steps-title">From wallet to receipt<br /><span>in about a minute.</span></h2></div>
        <p>No extension, seed phrase or terminal. The demo wallet is held by the server for your session, and you confirm every purchase yourself.</p></div>
      <ol>{steps.map(([title, text], index) => <li key={title}><span>0{index + 1}</span><h3>{title}</h3><p>{text}</p></li>)}</ol>
    </div></section>

    <section className="home-markets" aria-labelledby="markets-title"><div className="shell">
      <div className="home-head"><div><p className="eyebrow">Markets</p><h2 id="markets-title">Three instruments.<br /><span>Live on the demo chain.</span></h2></div>
        <p>Each instrument is a Zcash Shielded Asset issued on the demo chain. Prices are set by the demo dealer in ZEC. They are not market prices.</p></div>
      <MarketTable market={listing} />
    </div></section>

    <section className="home-proof" aria-labelledby="proof-title"><div className="shell">
      <div className="home-head"><div><p className="eyebrow eyebrow-light">What is verified</p><h2 id="proof-title">Three checks.<br />One transaction.</h2></div>
        <p>Proofs are verified before any wallet is asked to sign. The settlement coordinator verifies them again before it assembles the transaction.</p></div>
      <div className="home-proof-grid">
        <div className="home-proof-visual" role="img" aria-label="Verification rings: provenance, eligibility, settlement"><OfficialMotion kind="rings" className="rings-motion" /></div>
        <div className="home-proof-list">{proofs.map(proof => <div key={proof.name}><Image src={proof.src} alt="" width={44} height={44} /><div><h3>{proof.name}</h3><p>{proof.text}</p><small>{proof.status}</small></div></div>)}</div>
      </div>
    </div></section>

    <section className="home-close"><div className="shell">
      <OfficialMotion kind="logo" className="closing-motion" /><span className="sr-only">ZWA</span>
      <p>Proof, with boundaries.</p>
      {cta}
    </div></section>
  </>;
}
