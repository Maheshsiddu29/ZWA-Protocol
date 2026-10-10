//! Golden fixtures through the production verifier.
//!
//! - N = 3 rooted fixture (`freeze/FROZEN_ROOTED_FIXTURE.md`): Design A must be
//!   `LINEAGE_PROOF_VALID`; the rooted path must stop at the unwired
//!   authentication boundary (`PENDING_AUTHENTICATION`), never `VERIFIED`.
//! - CKN (`freeze/CKN_CHECKPOINT_COUNTEREXAMPLE.md`): its three step proofs are
//!   valid and join (research CKN-JOINS). Its rejection is a Layer-B matter
//!   (CHECKPOINTED_HISTORY on the CP view, UNKNOWN_FUTURE on the FV view). In
//!   Phase 1 it must therefore pass Design A and must not reach `VERIFIED`.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing
)]

mod common;

use common::{lineage, service};
use zwa_lineage::{
    FailureCode, LineageStatus, ObligationStatus, VerificationMode, VerifierIdentity,
};

#[test]
fn n3_design_a_is_lineage_proof_valid() {
    let ev = lineage("n3").evidence();
    assert_eq!(ev.len(), 3);
    let r = service().verify(VerificationMode::DesignAOnly, &ev);
    assert_eq!(
        r.status,
        LineageStatus::LineageProofValid,
        "{:?}",
        r.failure
    );
    assert_eq!(r.failure, None);
    assert_eq!(r.obligations.design_a, ObligationStatus::Passed);
    assert_eq!(r.evidence.steps, Some(3));
    assert_eq!(r.evidence.step_verify_times.len(), 3);
    assert_eq!(r.evidence.verifier, VerifierIdentity::FROZEN);
    assert!(!r.permits_trade_verification());
}

#[test]
fn n3_rooted_stops_at_the_unwired_authentication_boundary() {
    let ev = lineage("n3").evidence();
    let r = service().verify_rooted_lineage(&ev);
    assert_eq!(r.status, LineageStatus::PendingAuthentication);
    assert_eq!(
        r.failure.map(|f| f.code()),
        Some(FailureCode::ChainContextNotAuthenticated)
    );
    let o = r.obligations;
    assert_eq!(o.parse, ObligationStatus::Passed);
    assert_eq!(o.chain_context, ObligationStatus::Pending);
    assert_eq!(o.root, ObligationStatus::NotEvaluated);
    assert_eq!(o.design_a, ObligationStatus::Passed);
    assert_eq!(o.terminal, ObligationStatus::NotEvaluated);
    assert_eq!(o.context, ObligationStatus::NotEvaluated);
    assert_eq!(o.finality, ObligationStatus::NotEvaluated);
    assert!(!r.permits_trade_verification());
    assert!(r.evidence.lineage_statement_id.is_some());
    assert!(r.evidence.chain_ref_id.is_some());
}

#[test]
fn n3_wire_request_round_trips_and_verifies() {
    let l = lineage("n3");
    let bytes = l.request_bytes();
    // tag 16 + version 1 + vk_id 32 + chain_ref 117 + endpoints 64 + N 2
    // + 3 × (96 + 2 + 5120)
    assert_eq!(
        bytes.len(),
        16 + 1 + 32 + 117 + 64 + 2 + 3 * (96 + 2 + 5120)
    );
    let decoded = zwa_lineage::decode_request(&bytes).unwrap();
    assert_eq!(decoded.evidence, l.evidence());
    assert_eq!(
        zwa_lineage::encode_request(&decoded.evidence).unwrap(),
        bytes
    );

    let r = service().verify_request(VerificationMode::DesignAOnly, &bytes);
    assert_eq!(r.status, LineageStatus::LineageProofValid);
    let r = service().verify_request(VerificationMode::RootedLineage, &bytes);
    assert_eq!(r.status, LineageStatus::PendingAuthentication);
}

#[test]
fn n3_statement_id_is_keyed_on_the_statement_not_on_proof_bytes() {
    let l = lineage("n3");
    let id = l.evidence().lineage_statement_id().unwrap();
    // Same statement, different proof bytes (an invalid proof): same id.
    let mut other = l.clone();
    other.steps[1].proof = common::step("lb-s2b").proof;
    assert_eq!(other.evidence().lineage_statement_id().unwrap(), id);
    // Different chain_ref: different id.
    let mut moved = l.clone();
    moved.chain_ref = lineage("ckn").chain_ref;
    assert_ne!(moved.evidence().lineage_statement_id().unwrap(), id);
    // No chain_ref: no statement id.
    let mut none = l;
    none.chain_ref = None;
    assert_eq!(none.evidence().lineage_statement_id(), None);
}

#[test]
fn n3_result_json_is_complete_and_public() {
    let ev = lineage("n3").evidence();
    let j = service().verify_rooted_lineage(&ev).to_json();
    assert_eq!(j["status"], "PENDING_AUTHENTICATION");
    assert_eq!(j["code"], "CHAIN_CONTEXT_NOT_AUTHENTICATED");
    assert_eq!(j["verifier"]["verifier_id"], "lineage-v2-l1");
    assert_eq!(j["verifier"]["relation"], "NormalLineageStepV2");
    assert_eq!(
        j["verifier"]["vk_id"],
        "a2b349d7470504b6b5de3214f1783bc06d28c2f4e73111f9568652d0615b36b3"
    );
    assert_eq!(j["verifier"]["params_k"], 11);
    assert_eq!(j["obligations"]["design_a"], "PASSED");
    assert_eq!(j["obligations"]["chain_context"], "PENDING");
    assert_eq!(j["timing_us"]["steps"].as_array().unwrap().len(), 3);
}

#[test]
fn ckn_passes_design_a_like_the_research_recorded() {
    let ev = lineage("ckn").evidence();
    let r = service().verify(VerificationMode::DesignAOnly, &ev);
    assert_eq!(
        r.status,
        LineageStatus::LineageProofValid,
        "{:?}",
        r.failure
    );
}

#[test]
fn ckn_never_reaches_verified_in_phase_1() {
    let ev = lineage("ckn").evidence();
    let r = service().verify_rooted_lineage(&ev);
    assert_eq!(r.status, LineageStatus::PendingAuthentication);
    assert!(!r.permits_trade_verification());
    assert_eq!(r.obligations.design_a, ObligationStatus::Passed);
    assert_eq!(r.obligations.chain_context, ObligationStatus::Pending);
}

#[test]
fn other_research_lineages_pass_design_a() {
    // Branch B (CB-00c) and the wholly synthetic F-FOREIGN chain (R3-E): both
    // are Design-A valid. F-FOREIGN is rejected by the research only at ROOT
    // (CB-02b: ROOT_CMX), which shows why Design A alone is not VERIFIED.
    for name in ["branch_b", "foreign"] {
        let ev = lineage(name).evidence();
        let r = service().verify(VerificationMode::DesignAOnly, &ev);
        assert_eq!(
            r.status,
            LineageStatus::LineageProofValid,
            "{name}: {:?}",
            r.failure
        );
        let r = service().verify_rooted_lineage(&ev);
        assert_ne!(r.status, LineageStatus::RootedLineageVerified, "{name}");
    }
}

#[test]
fn fixture_copies_are_byte_identical_to_the_research_originals() {
    // The research checkout must sit next to this repository (the build
    // depends on it). Every copied proof equals its frozen original.
    let research =
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../ZWA-Lineage-Research");
    let steps = common::manifest()["steps"].as_object().unwrap();
    assert_eq!(steps.len(), 12);
    for (name, s) in steps {
        let original = std::fs::read(research.join(s["source"].as_str().unwrap())).unwrap();
        assert_eq!(original, common::step(name).proof, "{name}");
    }
}
