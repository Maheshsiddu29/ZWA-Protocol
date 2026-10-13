import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { DemoWallet } from "@/components/exchange/DemoWallet";
import { TryDemoButton } from "@/components/exchange/TryDemoButton";
import { WalletPairing } from "@/components/exchange/WalletPairing";
import { csrfToken, currentSession, demoMode } from "@/lib/exchange-auth";
import { walletStatus } from "@/lib/private-orders";

export const metadata: Metadata = { title: "Wallet" };
export const dynamic = "force-dynamic";

export default async function WalletPage() {
  const session = await currentSession();
  if (session?.role === "seller" && demoMode()) redirect("/seller");
  const status = session ? await walletStatus() : null;
  return <section className="page"><div className="shell">
    <header className="page-head"><div><p className="eyebrow">Wallet</p><h1>{session?.kind === "demo" ? "Your demo wallet" : "Wallet"}</h1>
      <p className="lede">{session?.kind === "demo" ? "A disposable shielded wallet created for this browser session. Its keys stay on the server and are deleted when the session ends."
        : demoMode() ? "Start a demo to get a disposable shielded wallet, funded with test ZEC, in a few seconds."
        : "Pair the wallet you control with this account. Trades are approved inside that wallet."}</p></div></header>
    {!session ? <div className="empty">
      <h2>{demoMode() ? "No wallet yet" : "Sign in first"}</h2>
      <p>{demoMode() ? "Each demo session gets its own wallet. No seed phrase, extension or terminal is needed." : "Wallet pairing belongs to a signed-in participant."}</p>
      {demoMode() ? <TryDemoButton /> : <Link className="button button-blue" href="/sign-in">Sign in</Link>}
    </div> : session.kind === "demo" ? <DemoWallet csrf={csrfToken(session)} initial={status} />
      : status ? <WalletPairing initial={status as Parameters<typeof WalletPairing>[0]["initial"]} csrf={csrfToken(session)} />
      : <div className="empty"><h2>Wallet service unavailable</h2><p>The exchange service is not configured for this account.</p></div>}
  </div></section>;
}
