"use strict";

// Phase 1B: an active credential authorizes exactly the Orchard receiver
// committed into its leaf.
//
// These tests cover the leaf and root arithmetic. Constraint-level enforcement
// lives in circuits/eligibility/rwa_investor_eligibility_v1.circom, which builds
// the leaf it proves membership for from the same receiver commitment that
// produces the trade's recipient commitment.

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  DOMAINS_0G,
  createHasher,
  computeCredential,
  computeCredentialV1,
  computeRecipientBinding,
  buildMerkleTree,
  merkleProof,
} = require("../../circuits/shared/eligibility-v1");

const fixture = require("../../tests/fixtures/phase1b-eligibility-v2.json");

const RECEIVER_A = fixture.receiver.approvedBytes;
const RECEIVER_B = fixture.receiver.substituteBytes;

const BASE = {
  subjectSecret: BigInt(fixture.credential.subjectSecret),
  credentialAuthorityCommitment: BigInt(fixture.credential.credentialAuthorityCommitment),
  investorClass: BigInt(fixture.credential.investorClass),
  jurisdiction: BigInt(fixture.credential.jurisdiction),
  credentialExpiry: BigInt(fixture.credential.credentialExpiry),
  credentialNonce: BigInt(fixture.credential.credentialNonce),
};

function issue(hash, receiverHex) {
  const binding = computeRecipientBinding(hash, BASE.subjectSecret, receiverHex);
  const credential = computeCredential(hash, {
    ...BASE,
    approvedReceiverCommitment: binding.receiverCommitment,
  });
  return { binding, credential };
}

test("the fixture credential leaf reproduces from the shared schema", async () => {
  const hash = await createHasher();
  const { credential, binding } = issue(hash, RECEIVER_A);
  assert.equal(credential.credentialLeaf.toString(), fixture.credential.credentialLeaf);
  assert.equal(credential.credentialMeta.toString(), fixture.credential.credentialMeta);
  assert.equal(
    binding.receiverCommitment.toString(),
    fixture.credential.approvedReceiverCommitment,
  );
});

test("the approved receiver commitment is the canonical recipient-binding receiver commitment", async () => {
  // Phase 1B must not introduce a second receiver encoding.
  const hash = await createHasher();
  const { binding, credential } = issue(hash, RECEIVER_A);
  const standalone = computeRecipientBinding(hash, BASE.subjectSecret, RECEIVER_A);
  assert.equal(binding.receiverCommitment, standalone.receiverCommitment);
  // And that value is genuinely an input to the leaf: changing it changes the leaf.
  const tampered = computeCredential(hash, {
    ...BASE,
    approvedReceiverCommitment: standalone.receiverCommitment + 1n,
  });
  assert.notEqual(tampered.credentialLeaf, credential.credentialLeaf);
});

test("approving a different receiver yields a different credential leaf", async () => {
  // Everything except the approved receiver is identical.
  const hash = await createHasher();
  const a = issue(hash, RECEIVER_A);
  const b = issue(hash, RECEIVER_B);
  assert.equal(a.credential.credentialMeta, b.credential.credentialMeta);
  assert.equal(a.credential.subjectCommitment, b.credential.subjectCommitment);
  assert.notEqual(a.credential.credentialLeaf, b.credential.credentialLeaf);
  assert.equal(
    b.credential.credentialLeaf.toString(),
    fixture.credentialApprovingSubstitute.credentialLeaf,
  );
});

test("same subject secret does not authorize an arbitrary receiver", async () => {
  // The decisive Phase 1B property: holding the subject secret is not enough,
  // because the leaf that must be in the authority's root names the receiver.
  const hash = await createHasher();
  const approved = issue(hash, RECEIVER_A);
  const attacker = issue(hash, RECEIVER_B);

  const leaves = fixture.credential.allLeaves.map(BigInt);
  const index = fixture.credential.credentialLeafIndex;
  assert.equal(leaves[index], approved.credential.credentialLeaf);

  const levels = buildMerkleTree(hash, leaves);
  const root = levels[levels.length - 1][0];
  assert.equal(root.toString(), fixture.credential.activeCredentialRoot);

  // Re-deriving the root with the attacker's leaf at the same position gives a
  // different root, so the attacker cannot satisfy the published one.
  const attackerLeaves = [...leaves];
  attackerLeaves[index] = attacker.credential.credentialLeaf;
  const attackerLevels = buildMerkleTree(hash, attackerLeaves);
  assert.notEqual(attackerLevels[attackerLevels.length - 1][0], root);

  // The authority's Merkle path for receiver A does not carry the B leaf up to
  // the published root either.
  const { siblings } = merkleProof(levels, index);
  let cursor = attacker.credential.credentialLeaf;
  let position = index;
  for (const sibling of siblings) {
    cursor = position % 2 === 0 ? hash([cursor, sibling]) : hash([sibling, cursor]);
    position >>= 1;
  }
  assert.notEqual(cursor, root);
});

test("the V2 credential leaf is domain-separated from the frozen V1 leaf", async () => {
  // A V1 leaf must never be readable as a V2 leaf.
  const hash = await createHasher();
  assert.notEqual(DOMAINS_0G.CREDENTIAL_V1, DOMAINS_0G.CREDENTIAL_V2);
  const v1 = computeCredentialV1(hash, BASE);
  const v2 = issue(hash, RECEIVER_A).credential;
  assert.notEqual(v1.credentialLeaf, v2.credentialLeaf);
  // The frozen Phase 0G leaf value is still reproducible for migrated evidence.
  assert.equal(
    v1.credentialLeaf.toString(),
    "15218271836826561578701755741896301127594556628574350368538319672252803018733",
  );
});

test("every credential field still binds into the V2 leaf", async () => {
  const hash = await createHasher();
  const base = issue(hash, RECEIVER_A);
  const receiverCommitment = base.binding.receiverCommitment;
  const mutations = {
    credentialAuthorityCommitment: BASE.credentialAuthorityCommitment + 1n,
    investorClass: BASE.investorClass + 1n,
    jurisdiction: BASE.jurisdiction + 1n,
    credentialExpiry: BASE.credentialExpiry + 1n,
    credentialNonce: BASE.credentialNonce + 1n,
    subjectSecret: BASE.subjectSecret + 1n,
  };
  for (const [field, value] of Object.entries(mutations)) {
    const mutated = computeCredential(hash, {
      ...BASE,
      [field]: value,
      approvedReceiverCommitment: receiverCommitment,
    });
    assert.notEqual(
      mutated.credentialLeaf,
      base.credential.credentialLeaf,
      `${field} must change the credential leaf`,
    );
  }
});

test("the Phase 1B fixture keeps the frozen Phase 0G trade commitment", async () => {
  assert.equal(
    fixture.trade.tradeCommitment,
    "10187400613857124614980227259922066295752635539032972479692659299555113110306",
  );
  // Receiver substitution is visible in the trade commitment as well.
  assert.notEqual(
    fixture.tradeWithSubstituteReceiver.tradeCommitment,
    fixture.trade.tradeCommitment,
  );
});
