export const site = {
  name: "ZWA Protocol",
  tagline: "Private assets. Verifiable origins.",
  description: "Experimental privacy infrastructure for Zcash Shielded Assets, with a working rooted-lineage verifier on pinned regtest research views.",
  github: "https://github.com/Maheshsiddu29/ZWA-Protocol",
  docs: "https://github.com/Maheshsiddu29/ZWA-Protocol/tree/main/docs",
  commits: "https://github.com/Maheshsiddu29/ZWA-Protocol/commits/main",
} as const;
export const primaryNav = [
  { label: "Exchange", href: "/exchange" },
  { label: "Protocol", href: "/protocol" },
  { label: "Demo", href: "/demo" },
  { label: "Research", href: "/research" },
  { label: "Developers", href: "/developers" },
] as const;
export const footerNav = [
  { title: "Explore", links: [{ label: "Exchange", href: "/exchange" }, { label: "Protocol", href: "/protocol" }, { label: "Verifier demo", href: "/demo" }, { label: "Research", href: "/research" }] },
  { title: "Build", links: [{ label: "Developers", href: "/developers" }, { label: "Documentation", href: site.docs, external: true }, { label: "GitHub", href: site.github, external: true }] },
  { title: "Project", links: [{ label: "About", href: "/about" }, { label: "Privacy", href: "/privacy" }, { label: "Terms", href: "/terms" }] },
] as const;
