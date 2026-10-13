import type { Metadata } from "next";
import { kindLabel, labReceipts, networkPins, publicReceipts, type PublicReceipt } from "@/lib/public-testnet";

export const metadata: Metadata = {
  title: "ZWA Testnet Verification",
  description: "Verification status and isolated laboratory receipts for experimental ZSA issuance and transfer.",
};

const fmt = (n: number | null | undefined) => (n == null ? "—" : n.toLocaleString("en-US"));
const when = (unix: number | null | undefined) => (unix ? new Date(unix * 1000).toISOString().replace(".000Z", " UTC").replace("T", " ") : "—");

function Fact({ label, value, mono = false }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return <div><span>{label}</span><strong className={mono ? "hash-value" : undefined}>{value}</strong></div>;
}

function ReceiptCard({ r }: { r: PublicReceipt }) {
  const lab = r.evidenceClass === "LOCAL_LAB_NOT_PUBLIC";
  const issuance = r.transaction.kind === "ZSA_ISSUANCE";
  return <article className="testnet-receipt">
    <div className="checkout-live-head">
      <div><p className="eyebrow">{lab ? "ISOLATED LAB · NOT PUBLIC TESTNET" : "Public chain evidence"}</p><h3>{kindLabel[r.transaction.kind]}</h3></div>
      <span className="read-only-badge">{lab ? "ISOLATED LAB" : r.inclusion.mined ? "INCLUDED" : "NOT YET INCLUDED"}</span>
    </div>

    <section className="testnet-panel">
      <p className="eyebrow">1 / {lab ? "Isolated lab inclusion" : "Public chain inclusion"}</p>
      <p className="demo-muted">{lab
        ? "Observed on a private loopback node running the community network's consensus code. The lab acceptance run also cross-checked Zebra RPC getrawtransaction, getblockhash, and getblock. These blocks are not publicly checkable."
        : "Anyone can repeat this check against the public lightwalletd. The txid is recomputed from the returned bytes, and the transaction is found in the block at the reported height."}</p>
      <div className="checkout-live-grid">
        <Fact label="Network" value={r.network.label} />
        <Fact label="Block 1 identity" value={r.network.block1Hash} mono />
        <Fact label="Transaction ID" value={r.transaction.txid} mono />
        <Fact label="Block height" value={fmt(r.inclusion.blockHeight)} />
        <Fact label="Block hash" value={r.inclusion.blockHash ?? "—"} mono />
        <Fact label="Observed confirmations" value={`${fmt(r.inclusion.confirmations)} (tip ${fmt(r.inclusion.observedTipHeight)} at ${when(r.inclusion.observedAtUnix)})`} />
        <Fact label="Network fee" value={`${fmt(r.transaction.feeZat)} zatoshis (node minimum ${fmt(r.transaction.nodeMinimumFeeZat)})`} />
        <Fact label="Format" value={`${r.transaction.version} · branch ${r.network.consensusBranchId} · ${r.transaction.orchardActions} Orchard actions · ${fmt(r.transaction.sizeBytes)} bytes`} />
        <Fact label="Verification source" value={r.inclusion.method} />
      </div>
    </section>

    {r.asset && <section className="testnet-panel">
      <p className="eyebrow">Experimental asset identity</p>
      <div className="checkout-live-grid">
        <Fact label="AssetBase" value={r.asset.assetBase ?? "—"} mono />
        {issuance && <Fact label="Asset description hash (ZIP-227)" value={r.asset.assetDescHash} mono />}
        {issuance && <Fact label="Issuer validating key" value={r.asset.issuerValidatingKey} mono />}
        {issuance && <Fact label="ZWA label (off-chain)" value={r.asset.label ?? "—"} />}
        {issuance && <Fact label="Issued amount · public by protocol" value={`${fmt(r.asset.issuedAmount)} units · ${r.asset.finalized ? "finalized" : "not finalized"}`} />}
        {!issuance && <Fact label="Transfer amount" value="Private. Shielded amounts are not visible on chain and are not published here." />}
      </div>
    </section>}

    <div className="checkout-product-grid">
      <section className="testnet-panel">
        <p className="eyebrow">2 / ZWA Groth16 authorization</p>
        <h4>{r.zwaAuthorization.groth16.replaceAll("_", " ")}</h4>
        <p className="demo-muted">{r.zwaAuthorization.note}</p>
      </section>
      <section className="testnet-panel">
        <p className="eyebrow">3 / Wallet observation</p>
        <h4>{(r.walletObservation.status ?? "UNKNOWN").replaceAll("_", " ")}</h4>
        <p className="demo-muted">{r.walletObservation.source}. A disposable ZWA wallet scanned the chain and saw the expected effect. This is not something the public chain shows.</p>
      </section>
    </div>
  </article>;
}

export default function TestnetVerificationPage() {
  return <>
    <header className="subpage-hero"><div className="shell">
      <p className="eyebrow">ZWA / Public testnet</p>
      <h1>ZWA Testnet Verification.</h1>
      <p>{publicReceipts.length
        ? "Owner-reviewed public testnet receipts appear below. Isolated lab receipts are labeled separately."
        : "Phase 2 evidence comes from an ISOLATED LAB using the community network's consensus rules. No public issuance or transfer has been broadcast."}</p>
    </div></header>

    <section className="subpage-section checkout-demo-section"><div className="shell"><div className="checkout-live">
      <div className="checkout-live-head">
        <div><p className="eyebrow">Public network identity</p><h2>Which chain this is.</h2>
          <p className="demo-muted">ZWA checks these values live before building, sending or verifying. If they change, the network was reset or replaced, and ZWA stops.</p></div>
        <span className="read-only-badge">{publicReceipts.length ? `${publicReceipts.length} PUBLIC RECEIPT${publicReceipts.length > 1 ? "S" : ""}` : "AWAITING OWNER-APPROVED BROADCAST"}</span>
      </div>
      <div className="checkout-live-grid">
        <Fact label="Network" value={networkPins.label} />
        <Fact label="Public lightwalletd" value={networkPins.lightwalletd} mono />
        <Fact label="Genesis hash" value={networkPins.genesisHash} mono />
        <Fact label="Block 1 hash" value={networkPins.block1Hash} mono />
        <Fact label="Consensus branch" value={`0x${networkPins.consensusBranchId} (NU7, ZSA)`} />
        <Fact label="Node" value={`${networkPins.nodeSubversion} · ${networkPins.zebraSource}`} mono />
      </div>

      {publicReceipts.length === 0 && <div className="checkout-empty testnet-empty"><img src="/illustrations/asset-tokens-onchain.svg" alt="" /><div>
        <h3>No public transaction yet</h3>
        <p>The issuance and transfer have been tested on an isolated lab node. Public faucet use and each public broadcast require separate owner approval. Public receipts appear only after public inclusion has been independently verified.</p>
      </div></div>}
      {publicReceipts.map((r) => <ReceiptCard key={r.transaction.txid} r={r} />)}

      <section className="testnet-panel">
        <p className="eyebrow">Verify it yourself</p>
        <p className="demo-muted">No public block explorer for this network was found, so verification uses the public lightwalletd gRPC service. With the <code>service.proto</code> from <code>zcash-shielded-assets/lightwalletd</code>:</p>
        <pre className="testnet-code">{`grpcurl -proto service.proto zsa.methyl.cc:443 \\
  cash.z.wallet.sdk.rpc.CompactTxStreamer/GetLightdInfo

# txid bytes are reversed (internal order), base64-encoded:
grpcurl -proto service.proto -d '{"hash":"<base64 reversed txid>"}' zsa.methyl.cc:443 \\
  cash.z.wallet.sdk.rpc.CompactTxStreamer/GetTransaction

grpcurl -proto service.proto -d '{"height":<blockHeight>}' zsa.methyl.cc:443 \\
  cash.z.wallet.sdk.rpc.CompactTxStreamer/GetBlock`}</pre>
        <p className="demo-muted">Check three things: the returned transaction hashes to the txid, the block at that height lists it, and the block is still on the chain whose block 1 is pinned above.</p>
      </section>

      <div className="checkout-product-grid">
        <section className="testnet-panel"><p className="eyebrow">Trust assumptions</p><ul className="testnet-list">
          <li>One independently operated lightwalletd (zsa.methyl.cc) and Zebra 5.0.0 node report inclusion. ZWA does not operate either.</li>
          <li>The network is experimental. A single operator mines it, it can be reset, and its coins have no value.</li>
          <li>The operator says the consensus code is <code>{networkPins.zebraSource}</code>. The node cannot attest to its own commit.</li>
          <li>Confirmations are counted from the tip at the time of observation. They are not finality.</li>
        </ul></section>
        <section className="testnet-panel"><p className="eyebrow">What this does not show</p><ul className="testnet-list">
          <li>Not an atomic swap and not ZIP-228 settlement. This network accepts one Action Group per transaction.</li>
          <li>Not gated by ZWA&apos;s Groth16 authorization. That human-approved atomic purchase exists only as separate private-regtest evidence on the <a href="/demo">demo page</a>.</li>
          <li>Not Zcash mainnet.</li>
          <li>Explorers and lightwalletd cannot see shielded transfer amounts or recipients, and this page does not claim they can.</li>
        </ul></section>
      </div>

      {labReceipts.length > 0 && <details className="checkout-history" open><summary>ISOLATED LAB ({labReceipts.length} transactions · NOT PUBLIC TESTNET)</summary>
        {labReceipts.map((r) => <ReceiptCard key={r.transaction.txid} r={r} />)}
      </details>}

      <p className="checkout-disclaimer">Experimental. ZWA asset labels are off-chain names; only the ZIP-227 description hash and AssetBase are on chain. Issued amounts are public because ZIP-227 issuance notes are plaintext. Recipient addresses, viewing keys, spending keys and wallet balances are never published.</p>
    </div></div></section>
  </>;
}
