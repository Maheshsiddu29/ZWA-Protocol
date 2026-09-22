"use strict";

const phase0f = require("./trade-commitment-v1");

const DOMAINS_0G = Object.freeze({
  SUBJECT_V1: 6004778564925477937n,
  RECEIVER_V1: 5928218449365324337n,
  RECIPIENT_BINDING_V1: 5927669780177634353n,
  CREDENTIAL_META_V1: 4851015908287927361n,
  CREDENTIAL_V1: 18949280892933681n,
  CREDENTIAL_V2: 18949280892933682n,
  ELIGIBILITY_POLICY_V1: 4993446657485917233n,
});

const MAX_U88 = (1n << 88n) - 1n;

function littleEndianBytesToBigInt(bytes) {
  const hex = Buffer.from(bytes).reverse().toString("hex");
  return hex.length === 0 ? 0n : BigInt(`0x${hex}`);
}

function bigIntToLittleEndianBytes(value, length) {
  const n = BigInt(value);
  if (n < 0n || n >= (1n << BigInt(length * 8))) throw new Error(`integer does not fit in ${length} bytes`);
  return Buffer.from(n.toString(16).padStart(length * 2, "0"), "hex").reverse();
}

function receiverToLimbs(receiverHex) {
  if (typeof receiverHex !== "string" || !/^[0-9a-fA-F]{86}$/.test(receiverHex)) {
    throw new Error("Orchard raw payment address must be exactly 43 bytes of hexadecimal");
  }
  const bytes = Buffer.from(receiverHex, "hex");
  return {
    limb0: littleEndianBytesToBigInt(bytes.subarray(0, 16)),
    limb1: littleEndianBytesToBigInt(bytes.subarray(16, 32)),
    limb2: littleEndianBytesToBigInt(bytes.subarray(32, 43)),
  };
}

function limbsToReceiver(limb0, limb1, limb2) {
  const values = [BigInt(limb0), BigInt(limb1), BigInt(limb2)];
  if (values[0] < 0n || values[0] > phase0f.MAX_U128 || values[1] < 0n || values[1] > phase0f.MAX_U128 || values[2] < 0n || values[2] > MAX_U88) {
    throw new Error("receiver limbs exceed 128/128/88-bit bounds");
  }
  return Buffer.concat([
    bigIntToLittleEndianBytes(values[0], 16),
    bigIntToLittleEndianBytes(values[1], 16),
    bigIntToLittleEndianBytes(values[2], 11),
  ]).toString("hex");
}

function computeRecipientBinding(hash, subjectSecret, receiverHex) {
  const receiverLimbs = receiverToLimbs(receiverHex);
  const subjectCommitment = hash([DOMAINS_0G.SUBJECT_V1, phase0f.assertField(subjectSecret, "subject secret")]);
  const receiverCommitment = hash([
    DOMAINS_0G.RECEIVER_V1,
    receiverLimbs.limb0,
    receiverLimbs.limb1,
    receiverLimbs.limb2,
  ]);
  const recipientCommitment = hash([
    DOMAINS_0G.RECIPIENT_BINDING_V1,
    subjectCommitment,
    receiverCommitment,
  ]);
  return { subjectCommitment, receiverCommitment, recipientCommitment, receiverLimbs };
}

// Phase 1B: the active credential leaf commits to the receiver the credential
// authority approved for this subject. `approvedReceiverCommitment` must be the
// canonical ReceiverCommitment from `computeRecipientBinding`; never a second,
// separately derived receiver hash.
function computeCredential(hash, fields) {
  const subjectCommitment = hash([
    DOMAINS_0G.SUBJECT_V1,
    phase0f.assertField(fields.subjectSecret, "subject secret"),
  ]);
  const credentialMeta = hash([
    DOMAINS_0G.CREDENTIAL_META_V1,
    phase0f.assertU64(fields.investorClass, "investor class"),
    phase0f.assertU64(fields.jurisdiction, "jurisdiction"),
    phase0f.assertU64(fields.credentialExpiry, "credential expiry"),
  ]);
  const credentialLeaf = hash([
    DOMAINS_0G.CREDENTIAL_V2,
    phase0f.assertField(fields.credentialAuthorityCommitment, "credential authority commitment"),
    subjectCommitment,
    credentialMeta,
    phase0f.assertU64(fields.credentialNonce, "credential nonce"),
    phase0f.assertField(fields.approvedReceiverCommitment, "approved receiver commitment"),
  ]);
  return { subjectCommitment, credentialMeta, credentialLeaf };
}

// The frozen Phase 0G credential leaf, retained only so migrated Phase 0G
// evidence stays reproducible. It approves no receiver and must not be used to
// build a new active credential root.
function computeCredentialV1(hash, fields) {
  const subjectCommitment = hash([
    DOMAINS_0G.SUBJECT_V1,
    phase0f.assertField(fields.subjectSecret, "subject secret"),
  ]);
  const credentialMeta = hash([
    DOMAINS_0G.CREDENTIAL_META_V1,
    phase0f.assertU64(fields.investorClass, "investor class"),
    phase0f.assertU64(fields.jurisdiction, "jurisdiction"),
    phase0f.assertU64(fields.credentialExpiry, "credential expiry"),
  ]);
  const credentialLeaf = hash([
    DOMAINS_0G.CREDENTIAL_V1,
    phase0f.assertField(fields.credentialAuthorityCommitment, "credential authority commitment"),
    subjectCommitment,
    credentialMeta,
    phase0f.assertU64(fields.credentialNonce, "credential nonce"),
  ]);
  return { subjectCommitment, credentialMeta, credentialLeaf };
}

function computePolicyLeaf(hash, investorClass, jurisdiction) {
  return hash([
    DOMAINS_0G.ELIGIBILITY_POLICY_V1,
    phase0f.assertU64(investorClass, "investor class"),
    phase0f.assertU64(jurisdiction, "jurisdiction"),
  ]);
}

module.exports = {
  ...phase0f,
  DOMAINS_0G,
  MAX_U88,
  receiverToLimbs,
  limbsToReceiver,
  computeRecipientBinding,
  computeCredential,
  computeCredentialV1,
  computePolicyLeaf,
};
