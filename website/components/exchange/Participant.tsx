import { redirect } from "next/navigation";
import type { ReactNode } from "react";
import { ExchangeShell } from "@/components/exchange/ExchangeShell";
import { csrfToken, currentSession, type Session } from "@/lib/exchange-auth";

/** Buyer-or-seller page; the role always comes from the signed session. */
export async function Participant({ eyebrow, title, intro, children }: { eyebrow: string; title: string; intro: string;
  children: (session: Session, csrf: string) => ReactNode }) {
  const session = await currentSession();
  if (!session || !["buyer", "seller"].includes(session.role)) redirect("/exchange/connect");
  return <ExchangeShell eyebrow={eyebrow} title={title} intro={intro}>
    <div className="exchange-participant"><span>Signed in as {session.id} · {session.role}</span><form action="/api/exchange/logout" method="post"><button type="submit">Sign out</button></form></div>
    {children(session, csrfToken(session))}
  </ExchangeShell>;
}
