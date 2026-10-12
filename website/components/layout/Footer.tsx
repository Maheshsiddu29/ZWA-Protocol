import Link from "next/link";
import { Logo } from "@/components/ui/Logo";
import { footerNav } from "@/lib/site";

export function Footer() {
  return <footer className="site-footer"><div className="shell">
    <div className="footer-top"><div><Logo className="footer-logo" /><p>Private assets.<br />Verifiable origins.</p></div>
      <nav aria-label="Footer" className="footer-links">{footerNav.map(col => <div key={col.title}><h2>{col.title}</h2>{col.links.map(link => "external" in link && link.external ? <a key={link.label} href={link.href} target="_blank" rel="noreferrer noopener">{link.label} ↗</a> : <Link key={link.label} href={link.href}>{link.label}</Link>)}</div>)}</nav>
    </div><div className="footer-bottom"><span>© 2026 ZWA Protocol</span><span>Experimental research · No production ZSA mainnet claims</span></div>
  </div></footer>;
}
