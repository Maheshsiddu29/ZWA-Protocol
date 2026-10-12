import { Protected } from "@/components/exchange/Protected";
import { assets } from "@/lib/exchange";

export const dynamic = "force-dynamic";

export default function IssuerPage() {
  return <Protected role="issuer" eyebrow="ISSUER / AUTHORITY" title="Issuer workspace." intro="Experimental issuance requires an independently controlled issuer wallet and an authenticated issuer root. Portal access does not grant mint authority.">
    <div className="exchange-list">{assets.map(asset=><div className="exchange-list-row" key={asset.id}><span className="exchange-list-number">↳</span><div><span className="exchange-eyebrow">DEFINED INSTRUMENT</span><h2>{asset.name}</h2><p>{asset.compliancePolicy}</p></div><div className="exchange-list-meta"><strong>Catalog definition</strong><small>No issuance action in browser</small></div></div>)}</div><p className="exchange-caveat">NAV statements can be authenticated by the local issuer adapter. No NAV has been published through this portal. The operator must inspect actual issuance txids and supply in the disposable wallet.</p>
  </Protected>;
}
