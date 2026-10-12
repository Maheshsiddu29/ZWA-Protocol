import { Protected } from "@/components/exchange/Protected";
import { boundActiveAsset } from "@/lib/exchange";

export const dynamic = "force-dynamic";
export default async function ActivityPage() {
  const active = await boundActiveAsset();
  const receipt = active?.checkout.receipt;
  return <Protected role="buyer" eyebrow="BUYER / ACTIVITY" title="Transaction activity." intro="Receipts show observed inclusion depth and wallet effects. A confirmation count is not irreversible finality.">
    {receipt && active?.checkout.status !== null ? <div className="exchange-order"><div><span>Instrument</span><strong>{active?.asset.name}</strong></div><div><span>Transaction</span><code>{receipt.txid}</code></div><div><span>Height</span><strong>{receipt.height}</strong></div><div><span>Observed depth</span><strong>{receipt.confirmations ?? "Unknown"} confirmations</strong></div><div><span>Finality label</span><strong>{receipt.finality.replaceAll("_", " ")}</strong></div></div> : <div className="exchange-empty"><h2>No current-chain receipt.</h2><p>Completion appears after the local node confirms inclusion. A stopped node never promotes a saved receipt to current evidence.</p></div>}
  </Protected>;
}
