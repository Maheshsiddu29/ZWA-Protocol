export const site = {
  name: "ZWA Exchange",
  tagline: "Private assets. Verifiable origins.",
  description: "An experimental private-asset exchange on Zcash shielded assets: zero-knowledge eligibility proofs and atomic ZSA-for-ZEC settlement on a disposable regtest chain.",
  github: "https://github.com/Maheshsiddu29/ZWA-Protocol",
} as const;

export const primaryNav = [
  { label: "Exchange", href: "/exchange" },
  { label: "Portfolio", href: "/portfolio" },
  { label: "Seller", href: "/seller" },
  { label: "Wallet", href: "/wallet" },
  { label: "Verify", href: "/verify" },
] as const;
