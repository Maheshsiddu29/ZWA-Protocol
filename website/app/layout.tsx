import type { Metadata, Viewport } from "next";
import "@fontsource-variable/inter";
import "./brand-tokens.css";
import "./globals.css";
import "./product.css";
import { Navbar } from "@/components/layout/Navbar";
import { Footer } from "@/components/layout/Footer";
import { currentSession, demoMode } from "@/lib/exchange-auth";
import { site } from "@/lib/site";

export const metadata: Metadata = {
  title: { default: `${site.name} — ${site.tagline}`, template: `%s · ${site.name}` },
  description: site.description,
  icons: { icon: "/brand/favicon.svg" },
  openGraph: { title: site.name, description: site.tagline, type: "website" },
};
// Remote mode renders pages from the hosted exchange's API; allow for its slowest read.
export const maxDuration = 30;
export const viewport: Viewport = { themeColor: "#000000", colorScheme: "light" };

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const session = await currentSession();
  const viewer = session ? { kind: session.kind, role: session.role, id: session.id } : null;
  return <html lang="en"><body>
    <Navbar viewer={viewer} demo={demoMode()} />
    <main id="main">{children}</main>
    <Footer />
  </body></html>;
}
