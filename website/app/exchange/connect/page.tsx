import { ExchangeShell } from "@/components/exchange/ExchangeShell";

export default async function ConnectPage({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const { error } = await searchParams;
  return <ExchangeShell eyebrow="LOCAL ACCESS" title="Enter your demo role." intro="This pairs a browser session with a provisioned local participant account. Wallet approval still happens inside the independent wallet terminal; no seed or signing key enters the browser.">
    <div className="exchange-auth"><form action="/api/exchange/login" method="post"><label htmlFor="id">Participant ID</label><input id="id" name="id" autoComplete="username" required maxLength={80}/><label htmlFor="password">Access password</label><input id="password" name="password" type="password" autoComplete="current-password" required/><button className="exchange-button" type="submit">Open participant area <span>↗</span></button>{error && <p role="alert">Access was denied. Check the locally provisioned credentials.</p>}</form><aside><span className="exchange-eyebrow">AUTHORIZATION BOUNDARY</span><h2>One participant. One role.</h2><p>Buyer, seller, issuer, and auditor sessions are verified on the server. A URL or frontend control cannot change the authenticated role.</p><p>Access accounts are provisioned by the local operator. No account is created from this form.</p></aside></div>
  </ExchangeShell>;
}
