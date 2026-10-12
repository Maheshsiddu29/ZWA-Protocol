"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Menu, X } from "lucide-react";
import { Logo } from "@/components/ui/Logo";
import { primaryNav } from "@/lib/site";

export function Navbar() {
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
  return <header className={`site-header ${dark ? "on-dark" : "on-light"}`}>
    <a href="#main" className="skip-link">Skip to content</a>
    <nav className="site-nav shell" aria-label="Primary">
      <Link href="/" className="brand-link" aria-label="ZWA Protocol home"><Logo inverse={dark} className="brand-logo" /></Link>
      <div className="nav-links">{primaryNav.map(item => <Link key={item.href} href={item.href} aria-current={pathname === item.href ? "page" : undefined}>{item.label}</Link>)}</div>
      <Link className="nav-cta" href="/demo">Explore the demo <span aria-hidden="true">↗</span></Link>
      <button className="menu-toggle" type="button" aria-label={open ? "Close menu" : "Open menu"} aria-expanded={open} aria-controls="mobile-menu" onClick={() => setOpen(!open)}>{open ? <X size={24} /> : <Menu size={24} />}</button>
    </nav>
    {open && <div id="mobile-menu" className="mobile-menu">{primaryNav.map(item => <Link href={item.href} key={item.href}>{item.label}</Link>)}</div>}
  </header>;
}
