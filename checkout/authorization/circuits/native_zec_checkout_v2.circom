pragma circom 2.2.2;

// Experimental checkout relation. Reuses PRE-EXISTING ZK-ORIGIN Poseidon and
// Merkle components without changing the frozen Phase 0F/1B circuits.
include "zk-origin/poseidon.circom";
include "zk-origin/merkle.circom";
include "zk-origin/comparators.circom";
include "circomlib/circuits/bitify.circom";

template NativeZecCheckoutV2() {
    signal input authorizedIssuanceRoot;
    signal input activeCredentialRoot;
    signal input tradeCommitment;
    signal input offeredAssetHi;
    signal input offeredAssetLo;
    signal input receiverLimb0;
    signal input receiverLimb1;
    signal input receiverLimb2;
    signal input quoteDigestHi;
    signal input quoteDigestLo;
    signal input txExpiryHeight;

    signal input issuerCommitment;
    signal input seriesCommitment;
    signal input policyRoot;
    signal input issuanceNonce;
    signal input issuanceMerkleSiblings[3];
    signal input issuanceMerklePathIndices[3];

    signal input subjectSecret;
    signal input credentialAuthorityCommitment;
    signal input investorClass;
    signal input jurisdiction;
    signal input credentialExpiryHeight;
    signal input credentialNonce;
    signal input credentialMerkleSiblings[3];
    signal input credentialMerklePathIndices[3];
    signal input allowedInvestorClass;
    signal input allowedJurisdiction;
    signal input policyMerkleSiblings[3];
    signal input policyMerklePathIndices[3];

    component a0 = Num2Bits(128); a0.in <== offeredAssetHi;
    component a1 = Num2Bits(128); a1.in <== offeredAssetLo;
    component r0 = Num2Bits(128); r0.in <== receiverLimb0;
    component r1 = Num2Bits(128); r1.in <== receiverLimb1;
    component r2 = Num2Bits(88); r2.in <== receiverLimb2;
    component q0 = Num2Bits(128); q0.in <== quoteDigestHi;
    component q1 = Num2Bits(128); q1.in <== quoteDigestLo;
    component e0 = Num2Bits(32); e0.in <== txExpiryHeight;
    component e1 = Num2Bits(32); e1.in <== credentialExpiryHeight;
    component inonce = Num2Bits(64); inonce.in <== issuanceNonce;
    component cnonce = Num2Bits(64); cnonce.in <== credentialNonce;
    component iclass = Num2Bits(8); iclass.in <== investorClass;
    component j = Num2Bits(16); j.in <== jurisdiction;
    component ac = Num2Bits(8); ac.in <== allowedInvestorClass;
    component aj = Num2Bits(16); aj.in <== allowedJurisdiction;

    // Phase 0F authorized issuance leaf, exact AssetBase limbs.
    component asset = PoseidonHash3();
    asset.in[0] <== 18387490596738609; // ASSETV1
    asset.in[1] <== offeredAssetHi;
    asset.in[2] <== offeredAssetLo;
    component issuanceMeta = PoseidonHash4();
    issuanceMeta.in[0] <== 5283658379176460593; // ISSMETA1
    issuanceMeta.in[1] <== seriesCommitment;
    issuanceMeta.in[2] <== policyRoot;
    issuanceMeta.in[3] <== issuanceNonce;
    component issuanceLeaf = PoseidonHash4();
    issuanceLeaf.in[0] <== 20639290677876273; // ISSUEV1
    issuanceLeaf.in[1] <== issuerCommitment;
    issuanceLeaf.in[2] <== asset.out;
    issuanceLeaf.in[3] <== issuanceMeta.out;
    component issuanceMembership = MerkleProofVerifier(3);
    issuanceMembership.leaf <== issuanceLeaf.out;
    issuanceMembership.root <== authorizedIssuanceRoot;
    for (var i = 0; i < 3; i++) {
        issuanceMembership.pathElements[i] <== issuanceMerkleSiblings[i];
        issuanceMembership.pathIndices[i] <== issuanceMerklePathIndices[i];
    }
    issuanceMembership.valid === 1;

    // Phase 1B CRED_V2 leaf: authority approval and checkout destination use
    // the same canonical raw Orchard receiver limbs.
    component subject = PoseidonHash2();
    subject.in[0] <== 6004778564925477937; // SUBJECT1
    subject.in[1] <== subjectSecret;
    component receiver = PoseidonHash4();
    receiver.in[0] <== 5928218449365324337; // RECEIVR1
    receiver.in[1] <== receiverLimb0;
    receiver.in[2] <== receiverLimb1;
    receiver.in[3] <== receiverLimb2;
    component credentialMeta = PoseidonHash4();
    credentialMeta.in[0] <== 4851015908287927361; // CREDMETA
    credentialMeta.in[1] <== investorClass;
    credentialMeta.in[2] <== jurisdiction;
    credentialMeta.in[3] <== credentialExpiryHeight;
    component credentialLeaf = PoseidonHash6();
    credentialLeaf.in[0] <== 18949280892933682; // CRED_V2
    credentialLeaf.in[1] <== credentialAuthorityCommitment;
    credentialLeaf.in[2] <== subject.out;
    credentialLeaf.in[3] <== credentialMeta.out;
    credentialLeaf.in[4] <== credentialNonce;
    credentialLeaf.in[5] <== receiver.out;
    component credentialMembership = MerkleProofVerifier(3);
    credentialMembership.leaf <== credentialLeaf.out;
    credentialMembership.root <== activeCredentialRoot;
    for (var c = 0; c < 3; c++) {
        credentialMembership.pathElements[c] <== credentialMerkleSiblings[c];
        credentialMembership.pathIndices[c] <== credentialMerklePathIndices[c];
    }
    credentialMembership.valid === 1;

    investorClass === allowedInvestorClass;
    jurisdiction === allowedJurisdiction;
    component policyLeaf = PoseidonHash3();
    policyLeaf.in[0] <== 4993446657485917233; // ELIGPOL1
    policyLeaf.in[1] <== allowedInvestorClass;
    policyLeaf.in[2] <== allowedJurisdiction;
    component policyMembership = MerkleProofVerifier(3);
    policyMembership.leaf <== policyLeaf.out;
    policyMembership.root <== policyRoot;
    for (var p = 0; p < 3; p++) {
        policyMembership.pathElements[p] <== policyMerkleSiblings[p];
        policyMembership.pathIndices[p] <== policyMerklePathIndices[p];
    }
    policyMembership.valid === 1;
    component validThroughTrade = ZKGreaterEqThan(32);
    validThroughTrade.in[0] <== credentialExpiryHeight;
    validThroughTrade.in[1] <== txExpiryHeight;
    validThroughTrade.out === 1;

    // Native ZEC is represented by the exact Phase 4B quote digest, whose
    // coordinator-defined encoding covers price, recipients, both fee types,
    // sponsor outpoint, network, branch, nonce and expiration. No fabricated
    // requested AssetBase is used. The host checks the digest against terms.
    component trade = PoseidonHash6();
    trade.in[0] <== 22062026277931314; // experimental native-ZEC V2 domain
    trade.in[1] <== asset.out;
    trade.in[2] <== receiver.out;
    trade.in[3] <== quoteDigestHi;
    trade.in[4] <== quoteDigestLo;
    trade.in[5] <== txExpiryHeight;
    trade.out === tradeCommitment;
}

component main {public [authorizedIssuanceRoot, activeCredentialRoot, tradeCommitment,
    offeredAssetHi, offeredAssetLo, receiverLimb0, receiverLimb1, receiverLimb2,
    quoteDigestHi, quoteDigestLo, txExpiryHeight, issuerCommitment,
    credentialAuthorityCommitment, policyRoot]} = NativeZecCheckoutV2();
