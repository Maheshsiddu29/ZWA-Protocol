"use strict";

// Regenerates tests/fixtures/phase1b-eligibility-v2.json.
//
// Phase 1B binds an authority-approved Orchard receiver into the active
// credential leaf. This script builds the deterministic synthetic evidence for
// that invariant: one credential that approves receiver A, and the adversarial
// witnesses that must fail against it.
//
// The trade parameters are taken unchanged from the frozen Phase 0G reference
// trade, so the resulting TradeCommitmentV1 must still equal the frozen Phase
// 0G golden value. Phase 1B changes the credential leaf, never the trade.
//
// Usage: node scripts/generate-phase1b-fixture.js

const fs = require("node:fs");
const path = require("node:path");
const schema = require("../circuits/shared/eligibility-v1");

const ROOT = path.join(__dirname, "..");
const PHASE_0G = path.join(ROOT, "tests/fixtures/eligible-reference-trade-v1.json");
const OUT = path.join(ROOT, "tests/fixtures/phase1b-eligibility-v2.json");

const PHASE_0G_GOLDEN_TRADE_COMMITMENT =
  "10187400613857124614980227259922066295752635539032972479692659299555113110306";

const CREDENTIAL_DEPTH = 3;
const CREDENTIAL_LEAF_INDEX = 3;

/// Deterministic filler leaves standing in for other subjects' credentials.
function fillerLeaf(hash, index) {
  return hash([BigInt(index + 1), BigInt(0xf111e4)]);
}

function buildCredentialTree(hash, credentialLeaf) {
  const leaves = [];
  for (let i = 0; i < 1 << CREDENTIAL_DEPTH; i += 1) {
    leaves.push(i === CREDENTIAL_LEAF_INDEX ? credentialLeaf : fillerLeaf(hash, i));
  }
  const levels = schema.buildMerkleTree(hash, leaves);
  const { siblings, pathIndices } = schema.merkleProof(levels, CREDENTIAL_LEAF_INDEX);
  return {
    leaves,
    root: levels[levels.length - 1][0],
    siblings,
    pathIndices,
  };
}

/// Builds one active credential that approves exactly `receiverHex`.
function issueCredential(hash, fields, receiverHex) {
  const binding = schema.computeRecipientBinding(hash, fields.subjectSecret, receiverHex);
  const credential = schema.computeCredential(hash, {
    ...fields,
    approvedReceiverCommitment: binding.receiverCommitment,
  });
  const tree = buildCredentialTree(hash, credential.credentialLeaf);
  return { binding, credential, tree };
}

/// Assembles a complete eligibility circuit input.
function eligibilityInput(trade, policy, issued, receiverLimbs, tradeCommitment) {
  const offered = schema.assetIdToLimbs(trade.offeredAssetBase);
  const requested = schema.assetIdToLimbs(trade.requestedAssetBase);
  return schema.stringifyBigInts({
    activeCredentialRoot: issued.tree.root,
    tradeCommitment,
    subjectSecret: BigInt(trade.subjectSecret),
    credentialAuthorityCommitment: BigInt(trade.credentialAuthorityCommitment),
    investorClass: BigInt(trade.investorClass),
    jurisdiction: BigInt(trade.jurisdiction),
    credentialExpiry: BigInt(trade.credentialExpiry),
    credentialNonce: BigInt(trade.credentialNonce),
    credentialMerkleSiblings: issued.tree.siblings,
    credentialMerklePathIndices: issued.tree.pathIndices.map(BigInt),
    policyRoot: BigInt(policy.policyRoot),
    allowedInvestorClass: BigInt(policy.investorClass),
    allowedJurisdiction: BigInt(policy.jurisdiction),
    policyMerkleSiblings: policy.merkleSiblings.map(BigInt),
    policyMerklePathIndices: policy.merklePathIndices.map(BigInt),
    receiverLimb0: receiverLimbs.limb0,
    receiverLimb1: receiverLimbs.limb1,
    receiverLimb2: receiverLimbs.limb2,
    offeredAssetHi: offered.hi,
    offeredAssetLo: offered.lo,
    offeredAmount: BigInt(trade.offeredAmount),
    requestedAssetHi: requested.hi,
    requestedAssetLo: requested.lo,
    requestedAmount: BigInt(trade.requestedAmount),
    matcherFeeAmount: BigInt(trade.matcherFeeAmount),
    matcherFeeRecipientCommitment: BigInt(trade.matcherFeeRecipientCommitment),
    nonce: BigInt(trade.nonce),
    expiry: BigInt(trade.expiry),
  });
}

/// Recomputes TradeCommitmentV1 for a given recipient commitment.
function tradeFor(hash, trade, recipientCommitment) {
  return schema.computeTrade(hash, {
    offeredAssetBase: trade.offeredAssetBase,
    requestedAssetBase: trade.requestedAssetBase,
    offeredAmount: BigInt(trade.offeredAmount),
    requestedAmount: BigInt(trade.requestedAmount),
    recipientCommitment,
    policyRoot: BigInt(trade.policyRoot),
    matcherFeeAmount: BigInt(trade.matcherFeeAmount),
    matcherFeeRecipientCommitment: BigInt(trade.matcherFeeRecipientCommitment),
    nonce: BigInt(trade.nonce),
    expiry: BigInt(trade.expiry),
  });
}

async function main() {
  const hash = await schema.createHasher();
  const phase0g = JSON.parse(fs.readFileSync(PHASE_0G, "utf8"));
  const { trade, policy, receiver } = phase0g;

  const receiverA = receiver.institutionABytes;
  const receiverB = receiver.institutionBBytes;
  const limbsA = schema.receiverToLimbs(receiverA);
  const limbsB = schema.receiverToLimbs(receiverB);

  const credentialFields = {
    subjectSecret: BigInt(trade.subjectSecret),
    credentialAuthorityCommitment: BigInt(trade.credentialAuthorityCommitment),
    investorClass: BigInt(trade.investorClass),
    jurisdiction: BigInt(trade.jurisdiction),
    credentialExpiry: BigInt(trade.credentialExpiry),
    credentialNonce: BigInt(trade.credentialNonce),
  };

  // The authority approves receiver A for this subject.
  const approvesA = issueCredential(hash, credentialFields, receiverA);
  // A second authority decision approving receiver B instead, used to show that
  // mutating only the approved receiver also breaks the proof.
  const approvesB = issueCredential(hash, credentialFields, receiverB);

  const tradeA = tradeFor(hash, trade, approvesA.binding.recipientCommitment);
  const tradeB = tradeFor(hash, trade, approvesB.binding.recipientCommitment);

  if (tradeA.tradeCommitment.toString() !== PHASE_0G_GOLDEN_TRADE_COMMITMENT) {
    throw new Error(
      `Phase 0G golden TradeCommitmentV1 regression: got ${tradeA.tradeCommitment}`,
    );
  }
  if (approvesA.binding.recipientCommitment.toString() !== trade.recipientCommitment) {
    throw new Error("recipient binding for receiver A no longer matches Phase 0G");
  }

  const fixture = {
    version: "Phase1B-RwaInvestorEligibility-CredV2",
    invariant:
      "An active credential authorizes exactly the Orchard receiver committed into its leaf. " +
      "A valid eligibility proof requires the authority-approved receiver commitment in the " +
      "credential leaf to equal the receiver commitment used by the trade RecipientCommitment.",
    notWalletControl:
      "Authority approval of a receiver is not proof that the trader currently controls that " +
      "receiver. Live wallet-control authentication is a separate later matcher concern.",
    credentialLeafV2:
      "Poseidon(CRED_V2, credentialAuthorityCommitment, subjectCommitment, credentialMeta, " +
      "credentialNonce, approvedReceiverCommitment)",
    domains: schema.stringifyBigInts({
      CREDENTIAL_META_V1: schema.DOMAINS_0G.CREDENTIAL_META_V1,
      CREDENTIAL_V1: schema.DOMAINS_0G.CREDENTIAL_V1,
      CREDENTIAL_V2: schema.DOMAINS_0G.CREDENTIAL_V2,
      RECEIVER_V1: schema.DOMAINS_0G.RECEIVER_V1,
      SUBJECT_V1: schema.DOMAINS_0G.SUBJECT_V1,
      RECIPIENT_BINDING_V1: schema.DOMAINS_0G.RECIPIENT_BINDING_V1,
    }),
    receiver: {
      canonicalType: "orchard::Address raw payment address",
      byteLength: 43,
      approvedBytes: receiverA,
      substituteBytes: receiverB,
      approvedLimbs: schema.stringifyBigInts(limbsA),
      substituteLimbs: schema.stringifyBigInts(limbsB),
    },
    credential: schema.stringifyBigInts({
      credentialAuthorityCommitment: credentialFields.credentialAuthorityCommitment,
      subjectSecret: credentialFields.subjectSecret,
      subjectCommitment: approvesA.credential.subjectCommitment,
      investorClass: credentialFields.investorClass,
      jurisdiction: credentialFields.jurisdiction,
      credentialExpiry: credentialFields.credentialExpiry,
      credentialNonce: credentialFields.credentialNonce,
      approvedReceiverCommitment: approvesA.binding.receiverCommitment,
      credentialMeta: approvesA.credential.credentialMeta,
      credentialLeaf: approvesA.credential.credentialLeaf,
      credentialLeafIndex: CREDENTIAL_LEAF_INDEX,
      activeCredentialRoot: approvesA.tree.root,
      merkleSiblings: approvesA.tree.siblings,
      merklePathIndices: approvesA.tree.pathIndices,
      allLeaves: approvesA.tree.leaves,
    }),
    credentialApprovingSubstitute: schema.stringifyBigInts({
      approvedReceiverCommitment: approvesB.binding.receiverCommitment,
      credentialLeaf: approvesB.credential.credentialLeaf,
      activeCredentialRoot: approvesB.tree.root,
      merkleSiblings: approvesB.tree.siblings,
      merklePathIndices: approvesB.tree.pathIndices,
    }),
    recipientBinding: schema.stringifyBigInts({
      approved: {
        subjectCommitment: approvesA.binding.subjectCommitment,
        receiverCommitment: approvesA.binding.receiverCommitment,
        recipientCommitment: approvesA.binding.recipientCommitment,
      },
      substitute: {
        subjectCommitment: approvesB.binding.subjectCommitment,
        receiverCommitment: approvesB.binding.receiverCommitment,
        recipientCommitment: approvesB.binding.recipientCommitment,
      },
    }),
    trade: schema.stringifyBigInts({
      ...trade,
      recipientCommitment: approvesA.binding.recipientCommitment,
      tradeCommitment: tradeA.tradeCommitment,
    }),
    tradeWithSubstituteReceiver: schema.stringifyBigInts({
      recipientCommitment: approvesB.binding.recipientCommitment,
      tradeCommitment: tradeB.tradeCommitment,
    }),
    // A: the authority-approved receiver. Must satisfy the circuit.
    approvedReceiverInput: eligibilityInput(
      trade,
      policy,
      approvesA,
      limbsA,
      tradeA.tradeCommitment,
    ),
    // B: same credential, same subject secret, same policy, but the trade is
    // fully self-consistent for receiver B. Only the credential leaf disagrees,
    // so this must fail on credential Merkle membership.
    substitutedReceiverInput: eligibilityInput(
      trade,
      policy,
      approvesA,
      limbsB,
      tradeB.tradeCommitment,
    ),
    // C: only the credential-approved receiver is mutated; the trade still
    // targets receiver A.
    mutatedApprovedReceiverInput: eligibilityInput(
      trade,
      policy,
      approvesB,
      limbsA,
      tradeA.tradeCommitment,
    ),
    // D: the credential approval is untouched but the receiver behind the trade
    // recipient is swapped while the trade commitment stays bound to A.
    mutatedTradeRecipientInput: eligibilityInput(
      trade,
      policy,
      approvesA,
      limbsB,
      tradeA.tradeCommitment,
    ),
  };

  fs.writeFileSync(OUT, `${JSON.stringify(fixture, null, 2)}\n`);
  process.stdout.write(`wrote ${path.relative(ROOT, OUT)}\n`);
  process.stdout.write(`activeCredentialRoot        ${approvesA.tree.root}\n`);
  process.stdout.write(`credentialLeaf (CRED_V2)    ${approvesA.credential.credentialLeaf}\n`);
  process.stdout.write(`approvedReceiverCommitment  ${approvesA.binding.receiverCommitment}\n`);
  process.stdout.write(`tradeCommitment             ${tradeA.tradeCommitment}\n`);
  process.stdout.write("Phase 0G golden TradeCommitmentV1 reproduced exactly\n");
}

main().then(
  () => process.exit(0),
  (error) => {
    process.stderr.write(`${error.stack}\n`);
    process.exit(1);
  },
);
