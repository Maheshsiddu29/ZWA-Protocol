/**
 * Public ZSA testnet verification evidence (NEW ZWA PROTOCOL WORK).
 *
 * Receipts are produced by `public-testnet/zwa-zsa-pub verify`, reviewed by the
 * owner and copied into `data/public-testnet/`. This module re-checks that a
 * receipt carries no private material before anything renders it.
 *
 * Claim discipline: public chain inclusion, ZWA Groth16 authorization and
 * private wallet observations are separate facts and are kept separate here.
 */

import publicFile from "@/data/public-testnet/receipts.json";
import labFile from "@/data/public-testnet/lab-rehearsal.json";

export const networkPins = {
  label: "Community ZSA Testnet (experimental)",
  lightwalletd: "https://zsa.methyl.cc",
  genesisHash: "03536e258babad397d59ce2995d9015d0791ac5272b8348e8f07f94143a72bb7",
  block1Hash: "01747c32a6bd1bbfe5d91566d26c190719181908fa7560d9ef89da2204e82274",
  consensusBranchId: "77190ad8",
  nodeSubversion: "/Zebra:5.0.0/",
  zebraSource: "zcash-shielded-assets/zebra@6a91831a6a9c18f667f59ab956c9437bf0bb5c5f",
  walletLibrary: "hhanh00/zkool2@51df4f0 (rlz)",
} as const;

export type PublicReceipt = {
  schema: "zwa-public-zsa-receipt/v1";
  evidenceClass: "PUBLIC_CHAIN_INCLUSION" | "LOCAL_LAB_NOT_PUBLIC";
  network: {
    label: string;
    lightwalletd: string;
    genesisHash: string;
    block1Hash: string;
    consensusBranchId: string;
    nodeSubversion: string;
    zebraSource: string;
    walletLibrary: string;
  };
  transaction: {
    kind: "ZSA_ISSUANCE" | "ZSA_SHIELDED_TRANSFER" | "LAB_FUNDING";
    txid: string;
    sha256: string;
    sizeBytes: number;
    version: string;
    feeZat: number;
    nodeMinimumFeeZat: number;
    expiryHeight: number;
    orchardActions: number;
  };
  inclusion: {
    mined: boolean;
    blockHeight: number | null;
    blockHash: string | null;
    blockTime: number | null;
    observedTipHeight: number;
    observedTipHash: string;
    confirmations: number;
    observedAtUnix: number;
    method: string;
  };
  asset: null | {
    label?: string | null;
    assetBase?: string | null;
    assetDescHash?: string;
    issuerValidatingKey?: string;
    finalized?: boolean;
    issuedAmount?: number;
    transferAmount?: string;
  };
  walletObservation: { source: string; status: string | null };
  zwaAuthorization: { groth16: string; note: string };
  trustAssumptions: string[];
  excludedClaims: string[];
};

const FORBIDDEN_KEYS = ["seed", "mnemonic", "spendingkey", "address", "ufvk", "fvk", "witness",
  "recipientassetbalance", "issuerassetbalance", "issuerorchardzat"];
const FORBIDDEN_PREFIXES = ["uregtest1", "utest1", "uviewregtest", "zregtestsapling1"];

/** Throws if a receipt contains anything that must stay private. */
export function assertPublicSafe(value: unknown, path = "receipt"): void {
  if (Array.isArray(value)) { value.forEach((v, i) => assertPublicSafe(v, `${path}[${i}]`)); return; }
  if (typeof value === "string") {
    if (FORBIDDEN_PREFIXES.some((p) => value.startsWith(p))) throw new Error(`${path} exposes an address or key`);
    return;
  }
  if (value && typeof value === "object") {
    for (const [k, v] of Object.entries(value)) {
      if (FORBIDDEN_KEYS.includes(k.toLowerCase())) throw new Error(`${path}.${k} is private`);
      assertPublicSafe(v, `${path}.${k}`);
    }
  }
}

function accept(raw: unknown, expected: PublicReceipt["evidenceClass"]): PublicReceipt[] {
  const list = (raw as { receipts?: unknown[] })?.receipts ?? [];
  return list.map((r) => {
    assertPublicSafe(r);
    const receipt = r as PublicReceipt;
    if (receipt.schema !== "zwa-public-zsa-receipt/v1") throw new Error("unknown receipt schema");
    if (receipt.evidenceClass !== expected) throw new Error(`receipt is ${receipt.evidenceClass}, expected ${expected}`);
    if (expected === "PUBLIC_CHAIN_INCLUSION" &&
        (receipt.network.genesisHash !== networkPins.genesisHash ||
         receipt.network.block1Hash !== networkPins.block1Hash ||
         receipt.network.consensusBranchId !== networkPins.consensusBranchId)) {
      throw new Error("public receipt does not match the pinned network");
    }
    return receipt;
  });
}

export const publicReceipts: PublicReceipt[] = accept(publicFile, "PUBLIC_CHAIN_INCLUSION");
export const labReceipts: PublicReceipt[] = accept(labFile, "LOCAL_LAB_NOT_PUBLIC");

export const kindLabel: Record<PublicReceipt["transaction"]["kind"], string> = {
  ZSA_ISSUANCE: "Experimental ZSA issuance",
  ZSA_SHIELDED_TRANSFER: "Shielded ZSA transfer",
  LAB_FUNDING: "Lab funding (tZEC)",
};
