import type { Metadata } from "next";
import { demoMode } from "@/lib/exchange-auth";
import { remoteEnabled } from "@/lib/remote-exchange";

export const metadata: Metadata = { title: "Sign in" };

export default async function SignInPage({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const { error } = await searchParams;
  // Remote mode serves only the judge demo; the dealer operator signs in on the exchange host itself.
  if (remoteEnabled()) return <section className="page"><div className="shell">
    <header className="page-head"><div><p className="eyebrow">Participants</p><h1>Sign in</h1>
      <p className="lede">No account is needed to try the exchange. Use Launch demo to get a disposable, funded wallet.</p></div></header>
    <div className="empty"><h2>Operator sign-in is not available on this site</h2>
      <p>The demo dealer is operated on the exchange host.</p></div>
  </div></section>;
  return <section className="page"><div className="shell">
    <header className="page-head"><div><p className="eyebrow">Participants</p><h1>Sign in</h1>
      <p className="lede">{demoMode() ? "For the operator of the demo dealer. To try the exchange as a buyer, use Launch demo instead; no account is needed." : "For provisioned buyer and seller accounts. A sign-in never authorizes a wallet spend."}</p></div></header>
    <div className="panel auth-card">
      <form action="/api/exchange/login" method="post">
        <label className="field-label" htmlFor="id">Account</label>
        <input className="field" id="id" name="id" autoComplete="username" required maxLength={80} />
        <label className="field-label" htmlFor="password">Password</label>
        <input className="field" id="password" name="password" type="password" autoComplete="current-password" required maxLength={512} />
        <button className="button button-blue" type="submit" style={{ marginTop: 8 }}>Sign in</button>
        {error && <p className="inline-error" role="alert">{error === "rate" ? "Too many attempts. Wait a minute and try again." : "Sign-in failed. Check the account and password."}</p>}
      </form>
    </div>
  </div></section>;
}
