import Link from "next/link";
import type { ReactNode } from "react";

const links = [
  { href: "/exchange", label: "Overview" }, { href: "/exchange/assets", label: "Browse assets" },
  { href: "/exchange/portfolio", label: "Portfolio" }, { href: "/exchange/orders", label: "Orders" },
  { href: "/exchange/wallet", label: "Wallet" },
  { href: "/exchange/activity", label: "Activity" },
];

export function ExchangeShell({ eyebrow, title, intro, children }: { eyebrow: string; title: string; intro: string; children: ReactNode }) {
  return <div className="exchange-page">
    <div className="exchange-bar"><div className="shell"><strong>ZWA EXCHANGE</strong><span>EXPERIMENTAL REGTEST · SYNTHETIC INSTRUMENTS</span><Link href="/exchange/connect">Pair local access ↗</Link></div></div>
      <div className="shell exchange-layout"><aside className="exchange-side" aria-label="Exchange navigation"><p>MARKETPLACE</p>{links.map(link=><Link key={link.href} href={link.href}>{link.label}</Link>)}<hr/><p>PARTICIPANTS</p><Link href="/seller">Seller</Link><Link href="/issuer">Issuer</Link><Link href="/auditor">Auditor</Link><hr/><Link href="/exchange/judge">Judge demonstration ↗</Link></aside>
      <div className="exchange-main"><header className="exchange-hero"><span className="exchange-eyebrow">{eyebrow}</span><h1>{title}</h1><p>{intro}</p></header>{children}</div>
    </div>
  </div>;
}
