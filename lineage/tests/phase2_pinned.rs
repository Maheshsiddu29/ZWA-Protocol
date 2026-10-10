//! Production rooted regressions using pinned public v3 fixtures only.
#![allow(clippy::unwrap_used, clippy::indexing_slicing)]
use sha2::{Digest, Sha256};
use zwa_layer_b_core::policy::ProvenancePolicy;
use zwa_lineage::phase2::{
    Phase2LineageVerifier, Phase2VerifierConfig, SnapshotChainView, SnapshotId, TradePermission,
};
use zwa_lineage::LineageStatus;

const POLICY: &[u8] = include_bytes!("fixtures/phase2/policy.bin");
const N3: &[u8] = include_bytes!("fixtures/phase2/n3-v3.bin");
const CKN: &[u8] = include_bytes!("fixtures/phase2/ckn-v3.bin");
fn verifier(id: SnapshotId) -> Phase2LineageVerifier {
    let view = SnapshotChainView::load(id).unwrap();
    let policy = ProvenancePolicy::decode(POLICY).unwrap();
    assert_eq!(
        hex::encode(Sha256::digest(POLICY)),
        "ce433a74be5510050d7f7f85cc1360f28c9e6bf71acbd68a1e7862a0925d14be"
    );
    Phase2LineageVerifier::new(
        view,
        Phase2VerifierConfig::new(policy.network_id, POLICY.to_vec(), policy.digest()),
    )
    .unwrap()
}
#[test]
fn pinned_rooted_statuses_and_gate() {
    for (id, bytes, expected) in [
        (SnapshotId::A7, N3, LineageStatus::RootedLineageVerified),
        (SnapshotId::A4, N3, LineageStatus::PendingFinality),
        (SnapshotId::Ckn, CKN, LineageStatus::CheckpointTrustRequired),
    ] {
        let result = verifier(id).verify_v3(bytes);
        assert_eq!(result.status, expected, "{:?}", result.failure);
        assert_eq!(
            TradePermission::from_result(&result).allowed,
            expected == LineageStatus::RootedLineageVerified
        );
    }
}
#[test]
fn pinned_negative_and_reorg() {
    let mut bad = N3.to_vec();
    bad[2527 + 100] ^= 1;
    let r = verifier(SnapshotId::A7).verify_v3(&bad);
    assert_eq!(r.status, LineageStatus::Rejected);
    assert!(!TradePermission::from_result(&r).allowed);
    let mut v = verifier(SnapshotId::A7);
    assert_eq!(v.verify_v3(N3).status, LineageStatus::RootedLineageVerified);
    v.set_view(SnapshotChainView::load(SnapshotId::B8).unwrap())
        .unwrap();
    let r = v.verify_v3(N3);
    assert_eq!(r.status, LineageStatus::InvalidatedByReorg);
    assert!(!TradePermission::from_result(&r).allowed);
}
