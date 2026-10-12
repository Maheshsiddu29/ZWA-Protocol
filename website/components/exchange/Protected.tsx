import { redirect } from "next/navigation";
import type { ReactNode } from "react";
import { ExchangeShell } from "@/components/exchange/ExchangeShell";
import { currentAccount, Role } from "@/lib/exchange-auth";

export async function Protected({ role, eyebrow, title, intro, children }: { role: Role; eyebrow: string; title: string; intro: string; children: ReactNode }) {
  const account = await currentAccount();
  if (!account || account.role !== role) redirect("/exchange/connect");
  return <ExchangeShell eyebrow={eyebrow} title={title} intro={intro}><div className="exchange-participant"><span>Signed in as {account.id} · {account.role}</span><form action="/api/exchange/logout" method="post"><button type="submit">Sign out</button></form></div>{children}</ExchangeShell>;
}
