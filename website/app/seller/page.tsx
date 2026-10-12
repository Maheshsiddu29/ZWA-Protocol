import Link from "next/link";
import { Protected } from "@/components/exchange/Protected";
import { assetById, boundActiveAsset } from "@/lib/exchange";
import { participantPortfolio } from "@/lib/persistent-portfolio";
import { market, ownOrders } from "@/lib/private-orders";
import { SellerPricing } from "@/components/exchange/SellerPricing";
import { csrfToken, currentSession } from "@/lib/exchange-auth";
import { stateLabel, zat } from "@/lib/order-labels";

export const dynamic = "force-dynamic";
export default async function SellerPage() {
  const portfolio = await participantPortfolio("seller");
  const orders = await ownOrders();
  const session = await currentSession();
  const offers = (await market())?.instruments.filter(item => item.sellerId) ?? [];
  const active = process.env.ZWA_PRIVATE_EXCHANGE_ROOT ? null : await boundActiveAsset();
  const scan = active?.checkout.walletScans;
  return <Protected role="seller" eyebrow="SELLER / LOCAL WALLET" title="Seller desk." intro="The seller wallet controls its own issued inventory and approves the exact terms independently. This portal observes the local session and never signs for the wallet.">
    {portfolio ? <><p className="exchange-caveat">Inventory rescanned at experimental chain height {portfolio.scanHeight.toLocaleString("en-US")} · {portfolio.state.replaceAll("_", " ")}. Wallet proceeds: {portfolio.nativeZat.toLocaleString("en-US")} zatoshis.</p>{portfolio.holdings.map(holding => { const receipt = portfolio.receipts.find(item => item.instrumentId === holding.instrumentId); return <div className="exchange-order" key={holding.assetBase}><div><span>Instrument</span><strong>{assetById(holding.instrumentId)?.name ?? holding.instrumentId}</strong></div><div><span>AssetBase</span><code>{holding.assetBase}</code></div><div><span>Current inventory</span><strong>{holding.units.toLocaleString("en-US")} units</strong></div>{receipt && <div><span>Received payment</span><strong>{receipt.paymentZat.toLocaleString("en-US")} zatoshis · tx {receipt.txid}</strong></div>}</div>; })}</> : active && scan ? <div className="exchange-order"><div><span>Instrument</span><strong>{active.asset.name}</strong></div><div><span>AssetBase</span><code>{active.checkout.quote?.assetId}</code></div><div><span>Inventory scan</span><strong>{scan.sellerBefore.assetUnits} → {scan.sellerAfter.assetUnits} units</strong></div><div><span>Native ZEC scan</span><strong>{scan.sellerBefore.nativeZat} → {scan.sellerAfter.nativeZat} zatoshis</strong></div><Link href="/demo#checkout">Inspect settlement ↗</Link></div> : <div className="exchange-empty"><h2>No reconciled sale is active.</h2><p>Offer creation and signing remain in the independent local seller wallet process.</p></div>}
    <p className="exchange-caveat"><Link href="/exchange/wallet">Seller wallet pairing ↗</Link></p>
    {session?.role === "seller" && offers.length > 0 && <SellerPricing offers={offers} csrf={csrfToken(session)} />}
    {orders?.length ? <h2 className="exchange-subhead">Incoming orders</h2> : null}
    {orders?.map(order => <Link className="exchange-order-row" key={order.id} href={`/exchange/orders/${order.id}`}><div><span className="exchange-eyebrow">{order.instrumentId} · buyer {order.buyerId}</span><strong>{order.awaitingWallet === "seller" ? "Your wallet approval is needed" : stateLabel[order.state] ?? order.state}</strong></div><div><span>{order.quantity} unit{order.quantity === 1 ? "" : "s"}</span><strong>{order.quote ? zat(order.quote.paymentZat) : "Quote pending"}</strong></div><div><span>{order.receipt ? `Received · tx ${order.receipt.txid.slice(0, 12)}…` : order.proofVerified ? "Groth16 verified" : "Proof pending"}</span><strong>Open ↗</strong></div></Link>)}
  </Protected>;
}
