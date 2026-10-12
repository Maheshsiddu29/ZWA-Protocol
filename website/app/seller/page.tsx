import Link from "next/link";
import { Protected } from "@/components/exchange/Protected";
import { boundActiveAsset } from "@/lib/exchange";

export const dynamic = "force-dynamic";
export default async function SellerPage() {
  const active = await boundActiveAsset();
  const scan = active?.checkout.walletScans;
  return <Protected role="seller" eyebrow="SELLER / LOCAL WALLET" title="Seller desk." intro="The seller wallet controls its own issued inventory and approves the exact terms independently. This portal observes the local session and never signs for the wallet.">
    {active && scan ? <div className="exchange-order"><div><span>Instrument</span><strong>{active.asset.name}</strong></div><div><span>AssetBase</span><code>{active.checkout.quote?.assetId}</code></div><div><span>Inventory scan</span><strong>{scan.sellerBefore.assetUnits} → {scan.sellerAfter.assetUnits} units</strong></div><div><span>Native ZEC scan</span><strong>{scan.sellerBefore.nativeZat} → {scan.sellerAfter.nativeZat} zatoshis</strong></div><Link href="/demo#checkout">Inspect settlement ↗</Link></div> : <div className="exchange-empty"><h2>No reconciled sale is active.</h2><p>Offer creation and signing remain in the independent local seller wallet process.</p></div>}
  </Protected>;
}
