import type { Metadata, Viewport } from "next";
import "@fontsource-variable/inter";
import "./brand-tokens.css";
import "./globals.css";
import "./exchange.css";
import "./judge.css";
import { Navbar } from "@/components/layout/Navbar";
import { Footer } from "@/components/layout/Footer";
import { site } from "@/lib/site";

export const metadata: Metadata = {
  title: { default: "ZWA Protocol — Private assets. Verifiable origins.", template: "%s · ZWA Protocol" },
  description: site.description,
  icons: { icon: "/brand/favicon.svg" },
  openGraph: { title: "ZWA Protocol", description: site.tagline, type: "website" },
};
export const viewport: Viewport = { themeColor: "#000000", colorScheme: "light" };
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body><Navbar /><main id="main">{children}</main><Footer /></body></html>;
}
