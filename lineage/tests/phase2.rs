//! Production Phase 2 path against pinned, authoritative Layer-B bytes.

#![allow(clippy::unwrap_used, clippy::expect_used, clippy::indexing_slicing)]

mod common;

use sha2::{Digest, Sha256};
use zwa_layer_b::chainref::{NetworkKind, ACTION_VK_ID, RULESET_TAG};
use zwa_layer_b::policy::{PolicyEntry, ProvenancePolicy, OPEN_END, STATUS_ACTIVE};
use zwa_layer_b::root::make_evidence as root_evidence;
use zwa_layer_b::terminal::make_evidence as terminal_evidence;
use zwa_layer_b::util::blake2b_256;
use zwa_layer_b::view::ChainView;

use zwa_lineage::phase2::TradePermission;
use zwa_lineage::phase2::{
    Phase2LineageVerifier, Phase2VerifierConfig, RootedRequestV3, SnapshotChainView, SnapshotId,
};
use zwa_lineage::{LineageStatus, RootedLineageEvidence};

fn research_view(id: SnapshotId) -> ChainView {
    assert_eq!(hex::encode(Sha256::digest(id.bytes())), id.sha256());
    let action_vk: [u8; 32] = hex::decode(ACTION_VK_ID).unwrap().try_into().unwrap();
    ChainView::load(id.bytes(), NetworkKind::Regtest, action_vk, RULESET_TAG).unwrap()
}

fn config(network: [u8; 32]) -> Phase2VerifierConfig {
    // All three fields are public issuance data in the pinned A7 snapshot.
    let view = research_view(SnapshotId::A7);
    let x0: [u8; 32] = common::lineage("n3").root_cmx.try_into().unwrap();
    let pos = *view.find(&x0).first().unwrap();
    let issuance = root_evidence(&view, pos, 4);
    let policy = ProvenancePolicy {
        policy_id: blake2b_256(b"ZWA_LB_PolicyDig", b"ZWA Layer-B research policy v1"),
        network_id: network,
        entries: vec![PolicyEntry {
            issuer_ik: issuance.issuer_ik,
            asset_desc_hash: issuance.asset_desc_hash,
            asset_base: issuance.asset_base,
            valid_from: 0,
            valid_until: OPEN_END,
            status: STATUS_ACTIVE,
        }],
    };
    // H1-ROOTED's one-entry I1/X policy. Layer-B's broader matrix adds a
    // second H1B entry and has a different digest (73bba1c6...).
    assert_eq!(
        hex::encode(policy.digest()),
        "cf6516c1d87c5bf5a1add3ec93cedb0638c97b23755a5a6b6cebe600449f22f2"
    );
    Phase2VerifierConfig::new(network, policy.encode(), policy.digest())
}

#[test]
#[ignore = "manual regeneration of pinned public v3 fixtures"]
fn materialize_v3_fixtures() {
    let dir = common::fixtures_dir().join("phase2");
    std::fs::create_dir_all(&dir).unwrap();
    let a7 = research_view(SnapshotId::A7);
    let x0: [u8; 32] = common::lineage("n3").root_cmx.try_into().unwrap();
    let issuance = root_evidence(&a7, *a7.find(&x0).first().unwrap(), 4);
    let policy = ProvenancePolicy {
        policy_id: blake2b_256(b"ZWA_LB_PolicyDig", b"ZWA Layer-B research policy v1"),
        network_id: a7.network_id,
        entries: vec![PolicyEntry {
            issuer_ik: issuance.issuer_ik,
            asset_desc_hash: issuance.asset_desc_hash,
            asset_base: issuance.asset_base,
            valid_from: 0,
            valid_until: OPEN_END,
            status: STATUS_ACTIVE,
        }],
    };
    std::fs::write(dir.join("policy.bin"), policy.encode()).unwrap();
    std::fs::write(
        dir.join("n3-v3.bin"),
        request(SnapshotId::A7, "n3").encode().unwrap(),
    )
    .unwrap();
    std::fs::write(
        dir.join("ckn-v3.bin"),
        request(SnapshotId::Ckn, "ckn").encode().unwrap(),
    )
    .unwrap();
}

fn request(id: SnapshotId, name: &str) -> RootedRequestV3 {
    let view = research_view(id);
    let evidence: RootedLineageEvidence = common::lineage(name).evidence();
    let root = *evidence.root_cmx().as_bytes();
    let terminal = *evidence.terminal_cmx().as_bytes();
    let height = evidence.chain_ref().unwrap().height();
    let root_pos = *view.find(&root).first().unwrap();
    let terminal_pos = *view.find(&terminal).first().unwrap();
    RootedRequestV3 {
        evidence,
        root_evidence: root_evidence(&view, root_pos, height)
            .encode()
            .try_into()
            .unwrap(),
        terminal_evidence: terminal_evidence(&view, terminal_pos, height)
            .encode()
            .try_into()
            .unwrap(),
    }
}

fn verifier(id: SnapshotId) -> Phase2LineageVerifier {
    let view = SnapshotChainView::load(id).unwrap();
    let cfg = config(view.network_id());
    Phase2LineageVerifier::new(view, cfg).unwrap()
}

#[test]
fn snapshots_hash_and_rederive() {
    for id in [
        SnapshotId::A4,
        SnapshotId::A7,
        SnapshotId::Cpa,
        SnapshotId::B8,
        SnapshotId::Ckn,
    ] {
        let view = SnapshotChainView::load(id).unwrap();
        assert_eq!(view.id(), id);
        let mut tampered = id.bytes().to_vec();
        tampered[100] ^= 1;
        assert!(SnapshotChainView::load_bytes(id, &tampered).is_err());
    }
    let pinned = [
        (
            "n3-v3.bin",
            "0821a7abafca556bdcf9403ed9e040603194bf5d46650316d42554ee9c96ebb0",
        ),
        (
            "ckn-v3.bin",
            "f1edfd8a4cae7a1efff33290dd3dded70b8ee6eb33382bc71a64dafc7910f5ef",
        ),
        (
            "policy.bin",
            "ce433a74be5510050d7f7f85cc1360f28c9e6bf71acbd68a1e7862a0925d14be",
        ),
    ];
    for (name, digest) in pinned {
        let bytes = std::fs::read(common::fixtures_dir().join("phase2").join(name)).unwrap();
        assert_eq!(hex::encode(Sha256::digest(bytes)), digest, "{name}");
    }
}

#[test]
fn n3_a7_a4_and_reorg() {
    let v3 = request(SnapshotId::A7, "n3");
    let bytes = v3.encode().unwrap();
    assert_eq!(
        RootedRequestV3::decode(&bytes).unwrap().encode().unwrap(),
        bytes
    );
    let mut active = verifier(SnapshotId::A7);
    let result = active.verify_v3(&bytes);
    assert_eq!(
        result.status,
        LineageStatus::RootedLineageVerified,
        "{:?}",
        result.failure
    );
    assert!(result.permits_trade_verification());
    assert_eq!(result.evidence.snapshot_id, Some("A7"));

    let a4 = verifier(SnapshotId::A4).verify_v3(&bytes);
    assert_eq!(
        a4.status,
        LineageStatus::PendingFinality,
        "{:?}",
        a4.failure
    );
    assert!(!a4.permits_trade_verification());

    active
        .set_view(SnapshotChainView::load(SnapshotId::B8).unwrap())
        .unwrap();
    let reorg = active.verify_v3(&bytes);
    assert_eq!(reorg.status, LineageStatus::InvalidatedByReorg);
    assert_eq!(reorg.failure.unwrap().research_code(), "FINALITY_VIOLATION");
    assert!(!reorg.permits_trade_verification());
}

#[test]
fn finality_tracker_observes_depth_and_nonfinal_reorg() {
    let bytes = request(SnapshotId::A7, "n3").encode().unwrap();
    let mut active = verifier(SnapshotId::A4);
    assert_eq!(
        active.verify_v3(&bytes).status,
        LineageStatus::PendingFinality
    );
    active
        .set_view(SnapshotChainView::load(SnapshotId::A7).unwrap())
        .unwrap();
    assert_eq!(
        active.verify_v3(&bytes).status,
        LineageStatus::RootedLineageVerified
    );

    let mut other = verifier(SnapshotId::A4);
    assert_eq!(
        other.verify_v3(&bytes).status,
        LineageStatus::PendingFinality
    );
    other
        .set_view(SnapshotChainView::load(SnapshotId::B8).unwrap())
        .unwrap();
    let invalidated = other.verify_v3(&bytes);
    assert_eq!(invalidated.status, LineageStatus::InvalidatedByReorg);
    assert_eq!(invalidated.failure.unwrap().research_code(), "INVALIDATED");
}

#[test]
fn ckn_needs_checkpoint_trust() {
    let req = request(SnapshotId::Ckn, "ckn");
    let result = verifier(SnapshotId::Ckn).verify_v3(&req.encode().unwrap());
    assert_eq!(
        result.status,
        LineageStatus::CheckpointTrustRequired,
        "{:?}",
        result.failure
    );
    assert_eq!(
        result.failure.unwrap().research_code(),
        "CHECKPOINTED_HISTORY"
    );
    assert!(!result.permits_trade_verification());
}

#[test]
fn checkpoint_mode_is_explicit_and_carries_its_scope() {
    let bytes = request(SnapshotId::A7, "n3").encode().unwrap();
    let view = SnapshotChainView::load(SnapshotId::Cpa).unwrap();
    let off = Phase2LineageVerifier::new(view, config(research_view(SnapshotId::Cpa).network_id))
        .unwrap();
    let pending = off.verify_v3(&bytes);
    assert_eq!(pending.status, LineageStatus::CheckpointTrustRequired);
    assert!(!TradePermission::from_result(&pending).allowed);

    let view = SnapshotChainView::load(SnapshotId::Cpa).unwrap();
    let trusted = Phase2LineageVerifier::new(
        view,
        config(research_view(SnapshotId::Cpa).network_id).trust_checkpoint_history(),
    )
    .unwrap();
    let accepted = trusted.verify_v3(&bytes);
    assert_eq!(
        accepted.status,
        LineageStatus::RootedLineageVerified,
        "{:?}",
        accepted.failure
    );
    assert_eq!(
        accepted.evidence.history_mode.unwrap().to_string(),
        "CP(c=4)"
    );

    let ckn = SnapshotChainView::load(SnapshotId::Ckn).unwrap();
    assert!(Phase2LineageVerifier::new(
        ckn,
        config(research_view(SnapshotId::Ckn).network_id).trust_checkpoint_history(),
    )
    .is_err());
}

#[test]
fn malformed_and_endpoint_evidence_deny_trade() {
    let mut req = request(SnapshotId::A7, "n3");
    req.root_evidence[145] ^= 1;
    let result = verifier(SnapshotId::A7).verify_v3(&req.encode().unwrap());
    assert_eq!(result.status, LineageStatus::Rejected);
    assert_eq!(result.failure.unwrap().research_code(), "ROOT_MERKLE");
    assert!(!result.permits_trade_verification());

    let mut req = request(SnapshotId::A7, "n3");
    req.terminal_evidence[4] ^= 1;
    let result = verifier(SnapshotId::A7).verify_v3(&req.encode().unwrap());
    assert_eq!(result.status, LineageStatus::Rejected);
    assert_eq!(result.failure.unwrap().research_code(), "TERM_MERKLE");

    let mut bytes = request(SnapshotId::A7, "n3").encode().unwrap();
    bytes.push(0);
    let result = verifier(SnapshotId::A7).verify_v3(&bytes);
    assert_eq!(result.status, LineageStatus::Rejected);
    assert!(!result.permits_trade_verification());

    let mut bytes = request(SnapshotId::A7, "n3").encode().unwrap();
    bytes[16] = 4;
    let result = verifier(SnapshotId::A7).verify_v3(&bytes);
    assert_eq!(result.status, LineageStatus::Unsupported);
    assert!(!result.permits_trade_verification());

    let mut bytes = request(SnapshotId::A7, "n3").encode().unwrap();
    bytes[17] ^= 1;
    let result = verifier(SnapshotId::A7).verify_v3(&bytes);
    assert_eq!(result.status, LineageStatus::Rejected);
    assert_eq!(result.failure.unwrap().research_code(), "DA_FOREIGN_VK");

    let mut bytes = request(SnapshotId::A7, "n3").encode().unwrap();
    bytes.truncate(230 + 1169 + 100);
    let result = verifier(SnapshotId::A7).verify_v3(&bytes);
    assert_eq!(result.status, LineageStatus::Rejected);
}

#[test]
fn cryptographic_and_chain_rejections_precede_finality() {
    let mut req = request(SnapshotId::A7, "n3");
    let mut steps = req.evidence.steps().to_vec();
    steps[0].proof = steps[1].proof.clone();
    req.evidence = RootedLineageEvidence::new(
        *req.evidence.root_cmx(),
        *req.evidence.terminal_cmx(),
        steps,
        req.evidence.chain_ref().copied(),
    )
    .unwrap();
    let bad_proof = verifier(SnapshotId::A4).verify_v3(&req.encode().unwrap());
    assert_eq!(bad_proof.status, LineageStatus::Rejected);
    assert_eq!(bad_proof.failure.unwrap().research_code(), "DA_PROOF(0)");

    let mut req = request(SnapshotId::A7, "n3");
    let mut steps = req.evidence.steps().to_vec();
    steps.swap(1, 2);
    req.evidence = RootedLineageEvidence::new(
        *req.evidence.root_cmx(),
        *req.evidence.terminal_cmx(),
        steps,
        req.evidence.chain_ref().copied(),
    )
    .unwrap();
    let broken_join = verifier(SnapshotId::A7).verify_v3(&req.encode().unwrap());
    assert_eq!(broken_join.status, LineageStatus::Rejected);
    assert_eq!(broken_join.failure.unwrap().research_code(), "DA_JOIN(0)");

    let mut bytes = request(SnapshotId::A7, "n3").encode().unwrap();
    // Last field of the 117-byte chain_ref is the canonical Orchard root.
    bytes[16 + 1 + 32 + 85] ^= 1;
    let bad_tree = verifier(SnapshotId::A7).verify_v3(&bytes);
    assert_eq!(bad_tree.status, LineageStatus::Rejected);
    assert_eq!(
        bad_tree.failure.unwrap().research_code(),
        "TREESTATE_MISMATCH"
    );
}

#[test]
fn gate_allows_only_verified() {
    let req = request(SnapshotId::A7, "n3");
    let mut result = verifier(SnapshotId::A7).verify_v3(&req.encode().unwrap());
    assert!(TradePermission::from_result(&result).allowed);
    for status in [
        LineageStatus::LineageProofValid,
        LineageStatus::PendingAuthentication,
        LineageStatus::PendingFinality,
        LineageStatus::InvalidatedByReorg,
        LineageStatus::CheckpointTrustRequired,
        LineageStatus::Rejected,
        LineageStatus::Unsupported,
        LineageStatus::InternalError,
    ] {
        result.status = status;
        let decision = TradePermission::from_result(&result);
        assert!(!decision.allowed, "{status}");
        assert_eq!(decision.reason, status.as_str());
    }
}

#[test]
fn selected_layer_b_rows_match_frozen_matrix() {
    let matrix: serde_json::Value = serde_json::from_str(include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../ZWA-Lineage-Research/post-h1f/layer-b/layer-b-result.json"
    )))
    .unwrap();
    let baseline = request(SnapshotId::A7, "n3");
    let view = research_view(SnapshotId::A7);
    let find_expected = |id: &str| {
        [
            "chainref_vectors",
            "root_vectors",
            "terminal_vectors",
            "combined_vectors",
        ]
        .iter()
        .flat_map(|group| matrix[*group].as_array().unwrap().iter())
        .find(|row| row["id"] == id)
        .unwrap()["actual"]
            .as_str()
            .unwrap()
            .to_string()
    };
    let check = |id: &str, bytes: &[u8]| {
        let result = verifier(SnapshotId::A7).verify_v3(bytes);
        let actual = if result.status == LineageStatus::RootedLineageVerified {
            "ACCEPT".to_string()
        } else {
            result.failure.unwrap().research_code()
        };
        let expected = find_expected(id);
        assert_eq!(actual, expected, "{id}");
        println!("{id}\t{expected}\t{actual}");
    };
    let bytes = baseline.encode().unwrap();
    check("CR-01", &bytes);
    check("RT-01", &bytes);
    check("TM-01", &bytes);
    check("CB-00", &bytes);

    let mut b = bytes.clone();
    b[102..134].fill(0);
    check("CR-02", &b);
    let mut b = bytes.clone();
    b[134..166].copy_from_slice(&view.roots[3].unwrap());
    check("CR-04", &b);
    let mut b = bytes.clone();
    b[134..166].copy_from_slice(&common::plus_q(&view.roots[4].unwrap()).unwrap());
    check("CR-05", &b);
    let mut b = bytes.clone();
    b[65] = 2;
    check("CR-06", &b);
    let mut b = bytes.clone();
    b[49] = b'X';
    check("CR-07", &b);
    let mut b = bytes.clone();
    b[98..102].copy_from_slice(&0u32.to_le_bytes());
    check("CR-09", &b);
    let mut b = bytes.clone();
    b[98..102].copy_from_slice(&9u32.to_le_bytes());
    check("CR-10", &b);
    let mut b = bytes.clone();
    b[66] ^= 1;
    check("CR-11", &b);
    let mut b = bytes.clone();
    b[102..134].copy_from_slice(&view.hashes[5]);
    check("CR-17", &b);
    for (id, height) in [("CR-18a", 5u32), ("CR-18b", 7)] {
        let mut b = bytes.clone();
        b[98..102].copy_from_slice(&height.to_le_bytes());
        b[102..134].copy_from_slice(&view.hashes[height as usize]);
        check(id, &b);
    }

    let mut req = baseline.clone();
    req.root_evidence[101] ^= 1;
    check("RT-07a", &req.encode().unwrap());
    let mut req = baseline.clone();
    let x0 = *req.evidence.root_cmx().as_bytes();
    let pos = *view.find(&x0).first().unwrap();
    req.root_evidence = root_evidence(&view, pos, 2).encode().try_into().unwrap();
    check("RT-08b", &req.encode().unwrap());
    let mut req = baseline.clone();
    req.root_evidence[145] ^= 1;
    check("RT-13a", &req.encode().unwrap());

    let mut req = baseline.clone();
    let out1p: [u8; 32] =
        common::unhex32("698a952e7831936fc1827c2b6c26d661e63af60487bf1735edfd39094522a71f");
    req.terminal_evidence = terminal_evidence(&view, *view.find(&out1p).first().unwrap(), 4)
        .encode()
        .try_into()
        .unwrap();
    check("TM-03b", &req.encode().unwrap());
    let mut req = baseline;
    req.terminal_evidence[4] ^= 1;
    check("TM-07-L0", &req.encode().unwrap());
}
