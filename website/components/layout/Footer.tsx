import Link from "next/link";
import { Logo } from "@/components/ui/Logo";
import { site } from "@/lib/site";

export function Footer() {
  return <footer className="site-footer"><div className="shell">
    <div className="footer-top">
      <div><Logo className="footer-logo" /><p>Private assets.<br />Verifiable origins.</p></div>
      <nav aria-label="Footer" className="footer-links">
        <div><h2>Exchange</h2><Link href="/exchange">Markets</Link><Link href="/portfolio">Portfolio</Link><Link href="/wallet">Wallet</Link></div>
        <div><h2>Participants</h2><Link href="/seller">Seller desk</Link><Link href="/sign-in">Sign in</Link><Link href="/verify">Verify</Link></div>
        <div><h2>Project</h2><a href={site.github} target="_blank" rel="noreferrer noopener">Source ↗</a><Link href="/privacy">Privacy</Link><Link href="/terms">Terms</Link></div>
      </nav>
    </div>
    <div className="footer-bottom"><span>© 2026 ZWA Protocol</span><span>Experimental software on a disposable test network. Assets have no value and confer no rights.</span></div>
  </div></footer>;
}
