//! Negative and adversarial cases. Every one must fail closed with a specific
//! code. Where the research recorded the same vector, the row id is cited and
//! the expected research code is the recorded one.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing
)]

mod common;

use common::{lineage, plus_q, service, step, Lineage};
use zwa_lineage::auth::{NotWiredRootAuthenticator, NotWiredTerminalAuthenticator};
use zwa_lineage::{
    AuthenticatedChainContext, ChainContextAuthenticator, ChainIdentityResult, ChainRef,
    FailureCode, FinalityResult, FrozenStepVerifier, LineageResult, LineageStatus,
    LineageVerificationService, ObligationStatus, UnavailableReason, VerificationFailure,
    VerificationMode, MAX_STEPS,
};

/// Expected outcome: status, production code, research code.
fn expect(r: &LineageResult, status: LineageStatus, code: FailureCode, research: &str) {
    let f = r
        .failure
        .unwrap_or_else(|| panic!("no failure, status {}", r.status));
    assert_eq!(
        (r.status, f.code(), f.research_code().as_str()),
        (status, code, research)
    );
    // The failure's own status is the result status.
    assert_eq!(f.status(), r.status);
    assert!(!r.permits_trade_verification());
}

fn design_a(l: &Lineage) -> LineageResult {
    service().verify(VerificationMode::DesignAOnly, &l.evidence())
}

fn parse_err(l: &Lineage) -> VerificationFailure {
    l.parse().unwrap_err()
}

fn wire(bytes: &[u8]) -> LineageResult {
    service().verify_request(VerificationMode::DesignAOnly, bytes)
}

fn rejected(r: &LineageResult, code: FailureCode, research: &str) {
    expect(r, LineageStatus::Rejected, code, research);
}

// 2. Zero steps.
#[test]
fn zero_steps() {
    let mut l = lineage("n3");
    l.steps.clear();
    let f = parse_err(&l);
    assert_eq!(
        (f.code(), f.research_code().as_str()),
        (FailureCode::EmptyLineage, "DA_EMPTY")
    );
    // Wire N = 0 is a framing error (CB-15c: MALFORMED).
    let mut bytes = lineage("n3").request_bytes();
    let n_at = 16 + 1 + 32 + 117 + 64;
    bytes[n_at..n_at + 2].copy_from_slice(&0u16.to_le_bytes());
    bytes.truncate(n_at + 2);
    rejected(&wire(&bytes), FailureCode::InvalidEncoding, "MALFORMED");
}

#[test]
fn too_many_steps() {
    let n3 = lineage("n3");
    let mut l = n3.clone();
    l.steps = std::iter::repeat_n(n3.steps[0].clone(), MAX_STEPS + 1).collect();
    let f = parse_err(&l);
    assert_eq!(
        (f.code(), f.research_code().as_str()),
        (FailureCode::TooManySteps, "MALFORMED")
    );
    // Wire N = 1025 (CB-15d: MALFORMED).
    let mut bytes = n3.request_bytes();
    let n_at = 16 + 1 + 32 + 117 + 64;
    bytes[n_at..n_at + 2].copy_from_slice(&1025u16.to_le_bytes());
    rejected(&wire(&bytes), FailureCode::InvalidEncoding, "MALFORMED");
}

// 3. Malformed proof bytes.
#[test]
fn flipped_proof_byte() {
    // CB-15k: second proof with one flipped byte → DA_PROOF(1).
    let mut l = lineage("n3");
    l.steps[1].proof[100] ^= 0x01;
    rejected(&design_a(&l), FailureCode::InvalidStepProof, "DA_PROOF(1)");
}

#[test]
fn garbage_proofs() {
    for fill in [0x00u8, 0xff, 0x5a] {
        let mut l = lineage("n3");
        l.steps[2].proof = vec![fill; 5120];
        rejected(&design_a(&l), FailureCode::InvalidStepProof, "DA_PROOF(2)");
    }
}

// 4. Wrong proof for tuple.
#[test]
fn swapped_proofs() {
    // DA-04: proofs of s0 and s1 exchanged, statements kept → Proof(0).
    let mut l = lineage("n3");
    let p0 = l.steps[0].proof.clone();
    l.steps[0].proof = l.steps[1].proof.clone();
    l.steps[1].proof = p0;
    rejected(&design_a(&l), FailureCode::InvalidStepProof, "DA_PROOF(0)");
}

#[test]
fn proof_of_a_sibling_tuple() {
    // s2b spends the same note as s2 (same cmx_old, nf_old) into out2b.
    let mut l = lineage("n3");
    l.steps[1].proof = step("lb-s2b").proof;
    rejected(&design_a(&l), FailureCode::InvalidStepProof, "DA_PROOF(1)");
}

#[test]
fn foreign_proof_replaces_a_step() {
    // P0-9 rejection fixture: one step proof replaced by a foreign proof.
    let mut l = lineage("n3");
    l.steps[2].proof = step("lb-fforeign2").proof;
    rejected(&design_a(&l), FailureCode::InvalidStepProof, "DA_PROOF(2)");
}

// 5–7. Mutated public inputs (still canonical): the proof no longer matches.
#[test]
fn mutated_public_inputs() {
    for (field, i) in [("cmx_old", 1usize), ("nf_old", 0), ("cmx_new", 2)] {
        let mut l = lineage("n3");
        let s = &mut l.steps[i];
        let target = match field {
            "cmx_old" => &mut s.cmx_old,
            "nf_old" => &mut s.nf_old,
            _ => &mut s.cmx_new,
        };
        target[0] ^= 0x01;
        let r = design_a(&l);
        rejected(&r, FailureCode::InvalidStepProof, &format!("DA_PROOF({i})"));
    }
}

// 8. Broken join with individually valid proofs.
#[test]
fn broken_join() {
    // [s1, s2b, s3]: out1 → out2b, then a step spending out2.
    let mut l = lineage("n3");
    l.steps[1] = step("lb-s2b");
    rejected(&design_a(&l), FailureCode::BrokenContinuity, "DA_JOIN(1)");
}

// 9. Wrong root.
#[test]
fn wrong_root() {
    let mut l = lineage("n3");
    l.root_cmx = l.steps[0].cmx_new.to_vec();
    rejected(&design_a(&l), FailureCode::RootMismatch, "DA_ROOT");
    // CB-13b: [s2, s3] with root x0.
    let mut l = lineage("n3");
    l.steps.remove(0);
    rejected(&design_a(&l), FailureCode::RootMismatch, "DA_ROOT");
}

// 10. Wrong terminal.
#[test]
fn wrong_terminal() {
    let mut l = lineage("n3");
    l.terminal_cmx = lineage("branch_b").terminal_cmx;
    rejected(&design_a(&l), FailureCode::TerminalMismatch, "DA_TERMINAL");
    // CB-14a: branch-B proof set with terminal out3.
    let mut l = lineage("branch_b");
    l.terminal_cmx = lineage("n3").terminal_cmx;
    rejected(&design_a(&l), FailureCode::TerminalMismatch, "DA_TERMINAL");
}

// 11. Reorder.
#[test]
fn reordered_steps() {
    // DA-03: edges in reverse order → Join(0).
    let mut l = lineage("n3");
    l.steps.reverse();
    rejected(&design_a(&l), FailureCode::BrokenContinuity, "DA_JOIN(0)");
    let mut l = lineage("n3");
    l.steps.swap(1, 2);
    rejected(&design_a(&l), FailureCode::BrokenContinuity, "DA_JOIN(0)");
}

// 12. Duplicate step: no duplicate elision.
#[test]
fn duplicated_step() {
    // DA-05: s0 duplicated → Join(0).
    let mut l = lineage("n3");
    let s0 = l.steps[0].clone();
    l.steps.insert(0, s0);
    rejected(&design_a(&l), FailureCode::BrokenContinuity, "DA_JOIN(0)");
    let mut l = lineage("n3");
    let last = l.steps[2].clone();
    l.steps.push(last);
    rejected(&design_a(&l), FailureCode::BrokenContinuity, "DA_JOIN(2)");
}

// 13. Truncated sequence.
#[test]
fn truncated_sequence() {
    // CB-13a: [s1, s3] with genuine endpoints → DA_JOIN(0).
    let mut l = lineage("n3");
    l.steps.remove(1);
    rejected(&design_a(&l), FailureCode::BrokenContinuity, "DA_JOIN(0)");
    // Last step dropped: proofs and joins hold, terminal does not.
    let mut l = lineage("n3");
    l.steps.pop();
    rejected(&design_a(&l), FailureCode::TerminalMismatch, "DA_TERMINAL");
}

// 14. Foreign verifier / VK.
#[test]
fn foreign_vk_in_request() {
    let mut bytes = lineage("n3").request_bytes();
    bytes[17] ^= 0x01; // first byte of vk_id
    rejected(&wire(&bytes), FailureCode::ForeignVerifier, "DA_FOREIGN_VK");
    let mut bytes = lineage("n3").request_bytes();
    bytes[17..49].copy_from_slice(&[0u8; 32]);
    rejected(&wire(&bytes), FailureCode::ForeignVerifier, "DA_FOREIGN_VK");
}

// 15. Malformed field encodings.
#[test]
fn non_canonical_fields() {
    // CB-15i: first step's cmx_old as cmx_old + q_P → DA_NONCANONICAL(0).
    let mut l = lineage("n3");
    l.steps[0].cmx_old = plus_q(&l.steps[0].cmx_old).unwrap();
    let f = parse_err(&l);
    assert_eq!(
        (f.code(), f.research_code().as_str()),
        (FailureCode::NonCanonicalField, "DA_NONCANONICAL(0)")
    );
    // CB-15j: root_cmx as x0 + q_P → ROOT_MALFORMED.
    let mut l = lineage("n3");
    l.root_cmx = plus_q(&l.root_cmx.clone().try_into().unwrap())
        .unwrap()
        .to_vec();
    assert_eq!(parse_err(&l).research_code(), "ROOT_MALFORMED");
    // Terminal → TERM_MALFORMED.
    let mut l = lineage("n3");
    l.terminal_cmx = vec![0xff; 32];
    assert_eq!(parse_err(&l).research_code(), "TERM_MALFORMED");
    // nf_old ≥ q_P at step 2.
    let mut l = lineage("n3");
    l.steps[2].nf_old = [0xff; 32];
    assert_eq!(parse_err(&l).research_code(), "DA_NONCANONICAL(2)");
    // Wrong lengths through the typed API.
    let mut l = lineage("n3");
    l.root_cmx.push(0);
    assert_eq!(parse_err(&l).research_code(), "ROOT_MALFORMED");
    // Through the wire: canonicalization after framing.
    let l = lineage("n3");
    let mut bytes = l.request_bytes();
    let step0 = 16 + 1 + 32 + 117 + 64 + 2;
    bytes[step0..step0 + 32].copy_from_slice(&plus_q(&l.steps[0].cmx_old).unwrap());
    rejected(
        &wire(&bytes),
        FailureCode::NonCanonicalField,
        "DA_NONCANONICAL(0)",
    );
}

// 16. Trailing proof garbage and proof length.
#[test]
fn proof_length_is_exact() {
    // DA-06 vector (s1 proof + 7 trailing bytes). The research verifier
    // ACCEPTS it (the transcript ignores trailing bytes); the contract (§G1,
    // §G2) requires production to REJECT it.
    let mut l = lineage("n3");
    l.steps[1].proof.extend_from_slice(&[0u8; 7]);
    let f = parse_err(&l);
    assert_eq!(
        (f.code(), f.research_code().as_str()),
        (FailureCode::NonCanonicalProof, "DA_NONCANONICAL_PROOF(1)")
    );
    let mut l = lineage("n3");
    l.steps[0].proof.pop();
    assert_eq!(parse_err(&l).research_code(), "DA_NONCANONICAL_PROOF(0)");
    let mut l = lineage("n3");
    l.steps[2].proof.clear();
    assert_eq!(parse_err(&l).research_code(), "DA_NONCANONICAL_PROOF(2)");
}

#[test]
fn proof_length_through_the_wire() {
    let l = lineage("n3");
    let mut long = l.clone();
    long.steps[1].proof.extend_from_slice(&[0u8; 7]);
    // Encode by hand: encode_request only accepts canonical evidence.
    let bytes = encode_raw(&long);
    rejected(
        &wire(&bytes),
        FailureCode::NonCanonicalProof,
        "DA_NONCANONICAL_PROOF(1)",
    );
    // A zero-length proof field is a framing error (research v1 rule).
    let mut empty = l.clone();
    empty.steps[0].proof.clear();
    rejected(
        &wire(&encode_raw(&empty)),
        FailureCode::InvalidEncoding,
        "MALFORMED",
    );
    // A proof field over 16384 bytes is a framing error.
    let mut huge = l;
    huge.steps[0].proof = vec![0u8; 16385];
    rejected(
        &wire(&encode_raw(&huge)),
        FailureCode::InvalidEncoding,
        "MALFORMED",
    );
}

#[test]
fn container_framing() {
    let good = lineage("n3").request_bytes();
    // CB-15a: one trailing byte.
    let mut b = good.clone();
    b.push(0);
    rejected(&wire(&b), FailureCode::InvalidEncoding, "MALFORMED");
    // CB-15b: truncated by one byte.
    rejected(
        &wire(&good[..good.len() - 1]),
        FailureCode::InvalidEncoding,
        "MALFORMED",
    );
    // CB-15f: wrong tag.
    let mut b = good.clone();
    b[0] ^= 0x01;
    rejected(&wire(&b), FailureCode::InvalidEncoding, "MALFORMED");
    // Empty and tiny inputs.
    rejected(&wire(&[]), FailureCode::InvalidEncoding, "MALFORMED");
    rejected(
        &wire(&good[..16]),
        FailureCode::InvalidEncoding,
        "MALFORMED",
    );
    // The research v1 object tag is not this container.
    let mut b = good;
    b[..16].copy_from_slice(b"ZWA-LB-LINEAGE:1");
    rejected(&wire(&b), FailureCode::InvalidEncoding, "MALFORMED");
}

// 17. chain_ref.
#[test]
fn missing_chain_ref() {
    let mut l = lineage("n3");
    l.chain_ref = None;
    let r = service().verify_rooted_lineage(&l.evidence());
    rejected(
        &r,
        FailureCode::ChainContextMissing,
        "CHAIN_CONTEXT_MISSING",
    );
    assert_eq!(r.obligations.design_a, ObligationStatus::NotEvaluated);
    // Version 2 has no encoding for an absent chain_ref.
    assert_eq!(
        zwa_lineage::encode_request(&l.evidence())
            .unwrap_err()
            .code(),
        FailureCode::ChainContextMissing
    );
}

#[test]
fn malformed_chain_ref() {
    let good = lineage("n3").request_bytes();
    let cr = 16 + 1 + 32;
    // Wrong chain_ref tag → MALFORMED.
    let mut b = good.clone();
    b[cr] ^= 0x01;
    rejected(&wire(&b), FailureCode::ChainRefMalformed, "MALFORMED");
    // CB-15h: embedded chain_ref version 2 → UNSUPPORTED_VERSION.
    let mut b = good.clone();
    b[cr + 16] = 2;
    expect(
        &wire(&b),
        LineageStatus::Unsupported,
        FailureCode::ChainRefUnsupportedVersion,
        "UNSUPPORTED_VERSION",
    );
    // Height 0 → MALFORMED (CR-09).
    let mut b = good.clone();
    b[cr + 49..cr + 53].copy_from_slice(&0u32.to_le_bytes());
    rejected(&wire(&b), FailureCode::ChainRefMalformed, "MALFORMED");
    // Non-canonical Orchard root (CR-05 shape).
    let mut b = good;
    b[cr + 85..cr + 117].copy_from_slice(&[0xff; 32]);
    rejected(&wire(&b), FailureCode::ChainRefMalformed, "MALFORMED");
}

// 18. Unsupported mode and version.
#[test]
fn unsupported_mode_and_version() {
    for name in ["", "fast", "batch", "Rooted", "design_a", "verified"] {
        let f = VerificationMode::parse(name).unwrap_err();
        assert_eq!(f.code(), FailureCode::UnsupportedMode);
        assert_eq!(f.status(), LineageStatus::Unsupported);
    }
    assert_eq!(
        VerificationMode::parse("design-a").unwrap(),
        VerificationMode::DesignAOnly
    );
    assert_eq!(
        VerificationMode::parse("rooted").unwrap(),
        VerificationMode::RootedLineage
    );
    for version in [0u8, 1, 3, 0xff] {
        let mut b = lineage("n3").request_bytes();
        b[16] = version;
        expect(
            &wire(&b),
            LineageStatus::Unsupported,
            FailureCode::UnsupportedVersion,
            "UNSUPPORTED_VERSION",
        );
    }
}

// 19. Authentication dependency unavailable or failing.
struct FailingChain(ChainIdentityResult);

impl ChainContextAuthenticator for FailingChain {
    fn authenticate_chain_ref(&self, _: &ChainRef) -> ChainIdentityResult {
        self.0
    }
    fn evaluate_finality(&self, _: &AuthenticatedChainContext) -> FinalityResult {
        FinalityResult::Unavailable(UnavailableReason::NotWired)
    }
}

struct PanickingChain;

impl ChainContextAuthenticator for PanickingChain {
    fn authenticate_chain_ref(&self, _: &ChainRef) -> ChainIdentityResult {
        panic!("adapter bug")
    }
    fn evaluate_finality(&self, _: &AuthenticatedChainContext) -> FinalityResult {
        panic!("adapter bug")
    }
}

fn with_chain<C: ChainContextAuthenticator>(
    chain: C,
) -> LineageVerificationService<C, NotWiredRootAuthenticator, NotWiredTerminalAuthenticator> {
    LineageVerificationService::new(
        FrozenStepVerifier::load().unwrap(),
        chain,
        NotWiredRootAuthenticator,
        NotWiredTerminalAuthenticator,
    )
}

#[test]
fn authentication_dependency_failures_fail_closed() {
    let ev = lineage("n3").evidence();
    // Phase 1 default: not wired → PENDING_AUTHENTICATION.
    let r = service().verify_rooted_lineage(&ev);
    expect(
        &r,
        LineageStatus::PendingAuthentication,
        FailureCode::ChainContextNotAuthenticated,
        "CHAIN_CONTEXT_NOT_AUTHENTICATED",
    );
    // Wired but failing at run time → INTERNAL_ERROR, Design A not even run.
    for reason in [
        UnavailableReason::ViewUnavailable,
        UnavailableReason::Timeout,
        UnavailableReason::NodeAborted,
    ] {
        let s = with_chain(FailingChain(ChainIdentityResult::Unavailable(reason)));
        let r = s.verify_rooted_lineage(&ev);
        expect(
            &r,
            LineageStatus::InternalError,
            FailureCode::AuthenticationUnavailable,
            "VIEW_UNAVAILABLE",
        );
        assert_eq!(r.obligations.chain_context, ObligationStatus::Failed);
        assert_eq!(r.obligations.design_a, ObligationStatus::NotEvaluated);
    }
    let s = with_chain(FailingChain(ChainIdentityResult::Rejected(
        VerificationFailure::layer_b(FailureCode::ChainContextRejected, "UNKNOWN"),
    )));
    expect(
        &s.verify_rooted_lineage(&ev),
        LineageStatus::Rejected,
        FailureCode::ChainContextRejected,
        "UNKNOWN",
    );
    let s = with_chain(FailingChain(ChainIdentityResult::Invalidated {
        finality_violation: true,
    }));
    expect(
        &s.verify_rooted_lineage(&ev),
        LineageStatus::InvalidatedByReorg,
        FailureCode::FinalityViolation,
        "FINALITY_VIOLATION",
    );
    let s = with_chain(FailingChain(ChainIdentityResult::Invalidated {
        finality_violation: false,
    }));
    expect(
        &s.verify_rooted_lineage(&ev),
        LineageStatus::InvalidatedByReorg,
        FailureCode::ReferenceInvalidated,
        "INVALIDATED",
    );
    // A panicking adapter is INTERNAL_ERROR, never a verdict.
    let s = with_chain(PanickingChain);
    expect(
        &s.verify_rooted_lineage(&ev),
        LineageStatus::InternalError,
        FailureCode::InternalVerifierError,
        "INTERNAL_VERIFIER_ERROR",
    );
}

#[test]
fn a_definitive_rejection_is_not_masked_by_a_pending_dependency() {
    let mut l = lineage("n3");
    l.steps[1].proof[100] ^= 0x01;
    let r = service().verify_rooted_lineage(&l.evidence());
    rejected(&r, FailureCode::InvalidStepProof, "DA_PROOF(1)");
    assert_eq!(r.obligations.chain_context, ObligationStatus::Pending);
    assert_eq!(r.obligations.design_a, ObligationStatus::Failed);
}

/// Encodes a version-2 request from raw parts, without canonicalization.
fn encode_raw(l: &Lineage) -> Vec<u8> {
    let mut v = b"ZWA-LINEAGE-REQ:".to_vec();
    v.push(2);
    v.extend_from_slice(&zwa_lineage::frozen::FROZEN_VK_ID);
    v.extend_from_slice(l.chain_ref.as_ref().unwrap());
    v.extend_from_slice(&l.root_cmx);
    v.extend_from_slice(&l.terminal_cmx);
    v.extend_from_slice(&u16::try_from(l.steps.len()).unwrap().to_le_bytes());
    for s in &l.steps {
        v.extend_from_slice(&s.cmx_old);
        v.extend_from_slice(&s.nf_old);
        v.extend_from_slice(&s.cmx_new);
        v.extend_from_slice(&u16::try_from(s.proof.len()).unwrap().to_le_bytes());
        v.extend_from_slice(&s.proof);
    }
    v
}
