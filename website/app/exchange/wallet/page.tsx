import { Participant } from "@/components/exchange/Participant";
import { WalletPairing } from "@/components/exchange/WalletPairing";
import { walletStatus } from "@/lib/private-orders";

export const dynamic = "force-dynamic";
export default async function WalletPage() {
  const status = await walletStatus();
  return <Participant eyebrow="WALLET" title="Your local wallet." intro="Pair the wallet you control with this account. Trades are approved inside that wallet, never in this browser.">
    {(session, csrf) => status ? <WalletPairing initial={status} csrf={csrf} />
      : <div className="exchange-empty"><h2>Wallet pairing is unavailable.</h2><p>The local exchange service is not configured for {session.id}.</p></div>}
  </Participant>;
}
