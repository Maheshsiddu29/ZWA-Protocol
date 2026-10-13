"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Menu, X } from "lucide-react";
import { Logo } from "@/components/ui/Logo";
import { TryDemoButton } from "@/components/exchange/TryDemoButton";
import { primaryNav } from "@/lib/site";

type Viewer = { kind: "account" | "demo"; role: "buyer" | "seller"; id: string } | null;

export function Navbar({ viewer, demo }: { viewer: Viewer; demo: boolean }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const update = () => setScrolled(window.scrollY > 48);
    update(); window.addEventListener("scroll", update, { passive: true });
    return () => window.removeEventListener("scroll", update);
  }, []);
  useEffect(() => setOpen(false), [pathname]);
  useEffect(() => {
    if (!open) return;
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [open]);
  const dark = pathname === "/" && !scrolled && !open;
  const active = (href: string) => pathname === href || pathname.startsWith(`${href}/`) ||
    (href === "/exchange" && pathname.startsWith("/orders/"));
  const status = viewer?.kind === "demo" ? <Link className="nav-session" href="/wallet"><span className="dot" />Demo wallet</Link>
    : viewer ? <Link className="nav-session" href={viewer.role === "seller" ? "/seller" : "/portfolio"}>{viewer.id}</Link>
    : demo ? <TryDemoButton className="nav-cta" label="Try demo" /> : <Link className="nav-cta-link" href="/sign-in">Sign in</Link>;
  return <header className={`site-header ${dark ? "on-dark" : "on-light"}`}>
    <a href="#main" className="skip-link">Skip to content</a>
    <nav className="site-nav shell" aria-label="Primary">
      <Link href="/" className="brand-link" aria-label="ZWA home"><Logo inverse={dark} className="brand-logo" /></Link>
      <div className="nav-links">{primaryNav.map(item => <Link key={item.href} href={item.href} aria-current={active(item.href) ? "page" : undefined}>{item.label}</Link>)}</div>
      <div className="nav-status">{status}</div>
      <button className="menu-toggle" type="button" aria-label={open ? "Close menu" : "Open menu"} aria-expanded={open} aria-controls="mobile-menu" onClick={() => setOpen(!open)}>{open ? <X size={24} /> : <Menu size={24} />}</button>
    </nav>
    {open && <div id="mobile-menu" className="mobile-menu">
      {primaryNav.map(item => <Link href={item.href} key={item.href} aria-current={active(item.href) ? "page" : undefined}>{item.label}</Link>)}
      <div className="mobile-status">{status}</div>
    </div>}
  </header>;
}
