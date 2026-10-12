import Image from "next/image";
import Link from "next/link";
import { OfficialMotion } from "@/components/motion/OfficialMotion";
import { FilmDialog } from "@/components/FilmDialog";

const assets = [
  { name: "Land", src: "/illustrations/asset-land.svg", caption: "The asset" },
  { name: "Home", src: "/illustrations/asset-home.svg", caption: "The livelihood" },
  { name: "Person", src: "/illustrations/asset-person.svg", caption: "The owner" },
  { name: "On-chain tokens", src: "/illustrations/asset-tokens-onchain.svg", caption: "The digital record" },
];
const proofs = [
  { name: "Provenance", src: "/illustrations/ring-provenance.svg", status: "Implemented relation", description: "A rooted-lineage verifier authenticates the issuance root and evaluates supported private lineage against a pinned regtest view." },
  { name: "Eligibility", src: "/illustrations/ring-eligibility.svg", status: "Research / not evaluated", description: "Credential and policy work exists, but the current verifier response does not evaluate investor eligibility." },
  { name: "Settlement", src: "/illustrations/ring-settlement.svg", status: "Experimental execution", description: "A recorded local ZSA-for-ZEC exchange demonstrates wallet-authorized execution. It is separate from the rooted-lineage proof." },
];
export default function Home() {
  return <>
    <section className="hero" aria-labelledby="hero-title">
      <div className="hero-backdrop" aria-hidden="true" />
      <div className="hero-content shell">
        <div className="hero-copy"><p className="eyebrow eyebrow-light">ZWA Protocol / Experimental infrastructure</p>
          <h1 id="hero-title">Private assets.<br /><span>Verifiable origins.</span></h1>
          <p className="hero-lede">A rooted-lineage verifier for supported private asset histories on Zcash research views. Authenticate the issuance root, follow the proof, and see the verdict without claiming more than the system verifies.</p>
          <div className="hero-actions"><Link className="button button-blue" href="/demo">Explore the Demo <span aria-hidden="true">↗</span></Link><FilmDialog /></div>
          <p className="hero-note">Research implementation · Pinned regtest fixtures · No production ZSA mainnet service</p>
        </div>
      </div>
      <div className="hero-bottom shell"><span>01 / The premise</span><span>Scroll to explore ↓</span></div>
    </section>

    <section className="story-section" aria-labelledby="story-title"><div className="shell">
      <p className="eyebrow">01 / Why privacy matters</p><div className="story-intro"><h2 id="story-title">Real assets have<br /><em>real lives</em> behind them.</h2><p>Land, homes, people, and commercial relationships should not become an open dossier because an asset moves onchain. A useful system proves a precise claim while limiting what it reveals.</p></div>
      <div className="asset-grid">{assets.map((asset, index) => <div className="asset-stage" key={asset.name}><span className="asset-index">0{index+1} / {asset.caption}</span><Image src={asset.src} alt={asset.name} width={240} height={220} /><strong>{asset.name}</strong></div>)}</div>
      <div className="story-bridge"><Image src="/illustrations/privacy-fingerprint.svg" alt="Official privacy fingerprint illustration" width={170} height={170} /><p>Private state enters the system.<br /><strong>A checkable claim comes out.</strong></p><OfficialMotion kind="fingerprint" className="fingerprint-motion" /></div>
    </div></section>

    <section className="approach-section" aria-labelledby="approach-title"><div className="shell">
      <p className="eyebrow">02 / The approach</p><div className="approach-head"><h2 id="approach-title">Three questions.<br /><span>One disciplined answer.</span></h2><p>These concepts form the protocol direction. Their current implementation status is different, so the boundaries are shown plainly.</p></div>
      <div className="proof-layout"><div className="proof-visual"><Image src="/illustrations/verification-rings.svg" alt="Three official verification rings: provenance, eligibility, settlement" width={520} height={520} /><OfficialMotion kind="rings" className="rings-motion" /></div><div className="proof-list">{proofs.map((proof, index) => <div className={`proof-row proof-${index}`} key={proof.name}><div className="proof-row-top"><Image src={proof.src} alt="" width={44} height={44} /><span>0{index+1}</span></div><h3>{proof.name}</h3><p>{proof.description}</p><small>{proof.status}</small></div>)}</div></div>
    </div></section>

    <section className="verify-section" aria-labelledby="verify-title"><div className="shell verify-grid"><div><p className="eyebrow eyebrow-light">03 / ZWA Verify</p><h2 id="verify-title">Follow the root.<br />Verify the lineage.</h2><p>The implemented verifier checks an authenticated issuance root, the supported private lineage relation, terminal authentication, and finality in an operator-owned view. A verdict is scoped to that view.</p><Link className="button button-white" href="/demo#verifier">Run a verifier scenario <span aria-hidden="true">↗</span></Link></div><ol className="verify-steps"><li><span>01</span><strong>Authenticated issuance root</strong><p>Anchor the claim in the pinned authorization policy and chain view.</p></li><li><span>02</span><strong>Supported private lineage</strong><p>Evaluate the proof sequence and continuity for the supported relation.</p></li><li><span>03</span><strong>Rooted verification verdict</strong><p>Read the status, obligations, trust source, and any failure code.</p></li></ol></div></section>

    <section className="checkout-section" aria-labelledby="checkout-title"><div className="shell checkout-grid"><div><p className="eyebrow">04 / ZWA Checkout</p><h2 id="checkout-title">A real recorded exchange.<br /><span>Clearly bounded evidence.</span></h2><p>In an owner-operated, disposable regtest run, independent wallets exchanged 7 ZSA units for 10,000 zatoshis. A sponsor paid the 5,000-zatoshi network fee. The recorded receipt is historical evidence, not a live purchase offer.</p><Link className="text-link" href="/demo#checkout">Inspect the receipt ↗</Link></div><div className="checkout-evidence"><div><span>Asset received</span><strong>7 <small>ZSA units</small></strong></div><div><span>Buyer paid</span><strong>10,000 <small>zatoshis</small></strong></div><div><span>Sponsored fee</span><strong>5,000 <small>zatoshis</small></strong></div><p>Recorded regtest · Current finality unknown when node is stopped · Purchased-note lineage unsupported · Eligibility not evaluated</p></div></div></section>

    <section className="research-section" aria-labelledby="research-title"><div className="shell research-grid"><div><p className="eyebrow">05 / Build with evidence</p><h2 id="research-title">Open the details.<br /><span>Inspect the limits.</span></h2></div><div><p>The API exposes verifier identity, fixed demo scenarios, rooted verification, and read-only checkout evidence. Research pages document assumptions and unsolved work.</p><div className="research-actions"><Link href="/developers">Developer API ↗</Link><Link href="/research">Research archive ↗</Link><Link href="/protocol">Protocol model ↗</Link></div></div></div></section>
    <section className="closing-section"><div className="shell"><div className="closing-logo"><OfficialMotion kind="logo" className="closing-motion" /><span className="sr-only">ZWA Protocol</span></div><p>Proof, with boundaries.</p><Link className="button button-blue" href="/demo">Explore the Demo ↗</Link></div></section>
  </>;
}
