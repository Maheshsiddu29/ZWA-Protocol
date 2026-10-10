//! Differential checks against the frozen research code (dev-dependencies
//! only; never linked into the library).
//!
//! 1. Design A: for every shared vector, the production verdict equals the
//!    frozen reference verifier's (`r3e/experiments/src/design_a.rs`,
//!    `Mode::Single`, under the frozen R2B-L1 `params()` / `keygen_vk()`), and,
//!    where the research recorded the same vector, the recorded outcome.
//!    Divergences are allowed only where the engineering contract or the
//!    Phase 1 brief mandates them; each is listed with its reason, and both
//!    sides must still reject.
//! 2. VK identity: production `vk_identity` equals research `proofs::vk_id`.
//! 3. Transcript exhaustion (§G2): every fixture proof is read to exactly its
//!    5,120th byte by the frozen transcript, which is why the exact-length rule
//!    is equivalent to rejecting trailing bytes.
//! 4. `chain_ref`: production encode / decode / id equal the research
//!    `layer-b` `chainref` module on the fixture references and the CR vectors.
//!
//! Set `LINEAGE_EVIDENCE_DIR` to also write the Design-A table as JSONL.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing
)]

mod common;

use std::io::Cursor;
use std::sync::OnceLock;

use common::{lineage, plus_q, service, step, Lineage};
use halo2_proofs::plonk::{self, SingleVerifier, VerifyingKey};
use halo2_proofs::poly::commitment::Params;
use halo2_proofs::transcript::{Blake2bRead, Challenge255};
use pasta_curves::{pallas, vesta};
use serde_json::json;
use zwa_lineage::{DesignAResult, VerificationMode};
use zwa_lineage_r2b_l1::public_inputs::{PublicInputsV2, PublicInstance};
use zwa_r3e_experiments::design_a::{self as research_da, Edge, Mode, Reject};

fn research_keys() -> &'static (Params<vesta::Affine>, VerifyingKey<vesta::Affine>) {
    static K: OnceLock<(Params<vesta::Affine>, VerifyingKey<vesta::Affine>)> = OnceLock::new();
    K.get_or_init(|| {
        let params = zwa_lineage_r2b_l1::prover::params();
        let vk = zwa_lineage_r2b_l1::prover::keygen_vk(&params);
        (params, vk)
    })
}

/// The frozen reference verifier on raw parts: "ACCEPT" or its research code.
fn research(l: &Lineage) -> String {
    let (params, vk) = research_keys();
    let root: [u8; 32] = l.root_cmx.clone().try_into().unwrap();
    let terminal: [u8; 32] = l.terminal_cmx.clone().try_into().unwrap();
    let edges: Vec<Edge> = l
        .steps
        .iter()
        .map(|s| Edge {
            x: PublicInputsV2 {
                cmx_old: s.cmx_old,
                nf_old: s.nf_old,
                cmx_new: s.cmx_new,
            },
            proof: s.proof.clone(),
        })
        .collect();
    match research_da::verify(params, vk, &root, &terminal, &edges, Mode::Single) {
        Ok(()) => "ACCEPT".into(),
        Err(Reject::Empty) => "DA_EMPTY".into(),
        Err(Reject::NonCanonical(i)) => format!("DA_NONCANONICAL({i})"),
        Err(Reject::Proof(i)) => format!("DA_PROOF({i})"),
        Err(Reject::Batch) => "DA_BATCH".into(),
        Err(Reject::Join(i)) => format!("DA_JOIN({i})"),
        Err(Reject::Root) => "DA_ROOT".into(),
        Err(Reject::Terminal) => "DA_TERMINAL".into(),
    }
}

/// The production verifier on the same raw parts: parse, then Design A.
fn production(l: &Lineage) -> String {
    match l.parse() {
        Err(f) => f.research_code(),
        Ok(ev) => match service().verify_design_a(&ev) {
            DesignAResult::LineageProofValid(_) => "ACCEPT".into(),
            DesignAResult::Rejected(f) => f.research_code(),
            DesignAResult::InternalError(f) => format!("INTERNAL({})", f.research_code()),
        },
    }
}

struct Case {
    id: &'static str,
    vector: &'static str,
    lineage: Lineage,
    /// Recorded research row and its recorded outcome, if one exists.
    recorded: Option<(&'static str, &'static str)>,
    /// The recorded row is a rooted-object row whose first failure precedes
    /// Design A (so it is compared with production, not with the Design-A-only
    /// reference). All other recorded rows end in Design A or accept.
    recorded_is_rooted_pre_design_a: bool,
    /// Contract-mandated divergence: (expected production code, reason).
    divergence: Option<(&'static str, &'static str)>,
}

fn n3_with(f: impl FnOnce(&mut Lineage)) -> Lineage {
    let mut l = lineage("n3");
    f(&mut l);
    l
}

fn ckn_with(f: impl FnOnce(&mut Lineage)) -> Lineage {
    let mut l = lineage("ckn");
    f(&mut l);
    l
}

fn cases() -> Vec<Case> {
    let c = |id, vector, lineage, recorded| Case {
        id,
        vector,
        lineage,
        recorded,
        recorded_is_rooted_pre_design_a: false,
        divergence: None,
    };
    let out3b = lineage("branch_b").terminal_cmx;
    let n3_root = lineage("n3").root_cmx;
    vec![
        c("D-N3", "N3 rooted fixture x0 → out3", lineage("n3"), Some(("RP-DA / CB-00", "ACCEPT"))),
        // The A2a-M conformance rows DA-01 … DA-06 are labelled "frozen rooted
        // N = 3 fixture" but are computed over the CKN proofs
        // (a2am_conf.rs reads h1-rooted/fixtures/ckn/proofs). They are compared
        // on the CKN lineage here.
        c("D-CKN", "CKN x2 → X → C2 → C3", lineage("ckn"), Some(("DA-01", "ACCEPT"))),
        c("D-BRANCH-B", "branch B x0 → out1 → out2b → out3b", lineage("branch_b"), Some(("CB-00c", "ACCEPT"))),
        c("D-FOREIGN", "R3-E F-FOREIGN N = 4", lineage("foreign"), None),
        c(
            "D-CKN-REV",
            "CKN edges reversed",
            ckn_with(|l| l.steps.reverse()),
            Some(("DA-03", "DA_JOIN(0)")),
        ),
        c(
            "D-CKN-SWAP",
            "CKN proofs of s0 and s1 exchanged",
            ckn_with(|l| {
                let p = l.steps[0].proof.clone();
                l.steps[0].proof = l.steps[1].proof.clone();
                l.steps[1].proof = p;
            }),
            Some(("DA-04", "DA_PROOF(0)")),
        ),
        c(
            "D-CKN-DUP",
            "CKN s0 duplicated",
            ckn_with(|l| {
                let s = l.steps[0].clone();
                l.steps.insert(0, s);
            }),
            Some(("DA-05", "DA_JOIN(0)")),
        ),
        Case {
            id: "D-CKN-TRAIL",
            vector: "CKN s1 proof with 7 trailing bytes",
            lineage: ckn_with(|l| l.steps[1].proof.extend_from_slice(&[0u8; 7])),
            recorded: Some(("DA-06", "ACCEPT")),
            recorded_is_rooted_pre_design_a: false,
            divergence: Some((
                "DA_NONCANONICAL_PROOF(1)",
                "contract §G1/§G2 (A2AM-F4): production rejects trailing proof bytes; the research verifier ignores them",
            )),
        },
        Case {
            id: "D-N3-SHORT",
            vector: "N3 first proof truncated to 5,119 bytes",
            lineage: n3_with(|l| {
                l.steps[0].proof.pop();
            }),
            recorded: None,
            recorded_is_rooted_pre_design_a: false,
            divergence: Some((
                "DA_NONCANONICAL_PROOF(0)",
                "contract §G2: wrong proof length is rejected before verification with its own code",
            )),
        },
        c("D-N3-13A", "N3 [s1, s3] with genuine endpoints", n3_with(|l| {
            l.steps.remove(1);
        }), Some(("CB-13a", "DA_JOIN(0)"))),
        c("D-N3-13B", "N3 [s2, s3] with root x0", n3_with(|l| {
            l.steps.remove(0);
        }), Some(("CB-13b", "DA_ROOT"))),
        c("D-N3-14A", "branch-B proof set with terminal out3", {
            let mut l = lineage("branch_b");
            l.terminal_cmx = lineage("n3").terminal_cmx;
            l
        }, Some(("CB-14a", "DA_TERMINAL"))),
        c("D-N3-15I", "N3 first cmx_old as cmx_old + q_P", n3_with(|l| {
            l.steps[0].cmx_old = plus_q(&l.steps[0].cmx_old).unwrap();
        }), Some(("CB-15i", "DA_NONCANONICAL(0)"))),
        c("D-N3-15K", "N3 second proof, byte 100 flipped", n3_with(|l| {
            l.steps[1].proof[100] ^= 1;
        }), Some(("CB-15k", "DA_PROOF(1)"))),
        Case {
            id: "D-N3-15J",
            vector: "N3 root_cmx as x0 + q_P",
            lineage: n3_with(|l| {
                l.root_cmx = plus_q(&n3_root.clone().try_into().unwrap()).unwrap().to_vec();
            }),
            recorded: Some(("CB-15j", "ROOT_MALFORMED")),
            recorded_is_rooted_pre_design_a: true,
            divergence: Some((
                "ROOT_MALFORMED",
                "the reference Design-A verifier compares endpoint bytes without canonicalizing them (DA_ROOT); production canonicalizes root_cmx first, which is what the research rooted object records (CB-15j)",
            )),
        },
        Case {
            id: "D-N3-TERM-NC",
            vector: "N3 terminal_cmx = 0xff…ff",
            lineage: n3_with(|l| l.terminal_cmx = vec![0xff; 32]),
            recorded: None,
            recorded_is_rooted_pre_design_a: false,
            divergence: Some((
                "TERM_MALFORMED",
                "Phase 1 brief §9: terminal_cmx is canonicalized before any proof check; the reference reaches DA_TERMINAL only after verifying every proof",
            )),
        },
        c("D-N3-SIB", "N3 step 1 carries the s2b proof", n3_with(|l| {
            l.steps[1].proof = step("lb-s2b").proof;
        }), None),
        c("D-N3-FOREIGN", "N3 step 2 carries a foreign proof", n3_with(|l| {
            l.steps[2].proof = step("lb-fforeign2").proof;
        }), None),
        c("D-N3-NF", "N3 nf_old of step 0 changed by one", n3_with(|l| {
            l.steps[0].nf_old[0] ^= 1;
        }), None),
        c("D-N3-CMXNEW", "N3 cmx_new of step 2 changed by one", n3_with(|l| {
            l.steps[2].cmx_new[0] ^= 1;
        }), None),
        c("D-N3-ZERO", "N3 proof 2 all zero", n3_with(|l| {
            l.steps[2].proof = vec![0u8; 5120];
        }), None),
        c("D-N3-JOIN", "N3 [s1, s2b, s3]", n3_with(|l| {
            l.steps[1] = step("lb-s2b");
        }), None),
        c("D-N3-TERM", "N3 with terminal out3b", n3_with(|l| {
            l.terminal_cmx = out3b;
        }), None),
        c("D-EMPTY", "N3 endpoints, no steps", n3_with(|l| l.steps.clear()), None),
    ]
}

#[test]
fn design_a_matches_the_frozen_reference() {
    let mut rows = Vec::new();
    let mut agree = 0;
    let mut diverge = 0;
    for case in cases() {
        let research = research(&case.lineage);
        let production = production(&case.lineage);
        if let Some((row, recorded)) = case.recorded {
            if case.recorded_is_rooted_pre_design_a {
                assert_eq!(
                    production, recorded,
                    "{}: production differs from recorded rooted row {row}",
                    case.id
                );
            } else {
                assert_eq!(
                    research, recorded,
                    "{}: live research differs from recorded row {row}",
                    case.id
                );
            }
        }
        let verdict = match case.divergence {
            None => {
                assert_eq!(production, research, "{}: {}", case.id, case.vector);
                agree += 1;
                "AGREE"
            }
            Some((expected, reason)) => {
                assert_eq!(production, expected, "{}: {}", case.id, reason);
                assert_ne!(
                    production, "ACCEPT",
                    "{}: a divergence may only be stricter",
                    case.id
                );
                diverge += 1;
                "CONTRACT_DIVERGENCE"
            }
        };
        rows.push(json!({
            "id": case.id,
            "vector": case.vector,
            "research_live": research,
            "recorded": case.recorded.map(|(row, out)| json!({
                "row": row,
                "outcome": out,
                "compared_with": if case.recorded_is_rooted_pre_design_a { "production" } else { "research_live" },
            })),
            "production": production,
            "verdict": verdict,
            "reason": case.divergence.map(|d| d.1),
        }));
    }
    println!(
        "design-a differential: {agree} agree, {diverge} contract divergences, 0 disagreements"
    );
    if let Ok(dir) = std::env::var("LINEAGE_EVIDENCE_DIR") {
        let text: String = rows.iter().map(|r| format!("{r}\n")).collect();
        std::fs::write(std::path::Path::new(&dir).join("differential.jsonl"), text).unwrap();
    }
}

#[test]
fn vk_identity_matches_research() {
    let (_, vk) = research_keys();
    let production = zwa_lineage::frozen::vk_identity(vk);
    let research = zwa_r3e_experiments::proofs::vk_id(vk);
    assert_eq!(hex::encode(production), research);
    assert_eq!(research, zwa_r3e_experiments::proofs::R2B_L1_VK);
    assert_eq!(service().identity().vk_id, production);
}

#[test]
fn every_fixture_proof_exhausts_its_transcript_at_5120_bytes() {
    let (params, vk) = research_keys();
    let names = [
        "lb-s1", "lb-s2", "lb-s3", "lb-s2b", "lb-s3b", "ckn-s0", "ckn-s1", "ckn-s2",
    ];
    for name in names {
        let s = step(name);
        let x = PublicInputsV2 {
            cmx_old: s.cmx_old,
            nf_old: s.nf_old,
            cmx_new: s.cmx_new,
        };
        let column = PublicInstance::parse(&x).unwrap().column();
        let instances: &[&[pallas::Base]] = &[&column];
        let mut padded = s.proof.clone();
        padded.extend_from_slice(&[0xAB; 64]);
        let mut cursor = Cursor::new(padded.as_slice());
        let mut transcript = Blake2bRead::<_, vesta::Affine, Challenge255<_>>::init(&mut cursor);
        let ok = plonk::verify_proof(
            params,
            vk,
            SingleVerifier::new(params),
            &[instances],
            &mut transcript,
        );
        assert!(ok.is_ok(), "{name}");
        assert_eq!(cursor.position(), 5120, "{name}");
    }
}

#[test]
fn chain_ref_matches_research_layer_b() {
    use zwa_layer_b::chainref::ChainRef as ResearchChainRef;
    for name in ["n3", "ckn"] {
        let bytes = lineage(name).chain_ref.unwrap();
        let research = ResearchChainRef::decode(&bytes).unwrap();
        let production = zwa_lineage::ChainRef::decode(&bytes).unwrap();
        assert_eq!(research.encode(), production.encode().to_vec(), "{name}");
        assert_eq!(research.id(), production.id(), "{name}");
        assert_eq!(research.height, production.height());
        assert_eq!(&research.network_id, production.network_id());
        assert_eq!(&research.block_hash, production.block_hash());
        assert_eq!(&research.orchard_root, production.orchard_root().as_bytes());
    }
    // Every single-byte mutation of the N3 reference, plus truncation and
    // extension: same accept/reject decision and same error class.
    let good = lineage("n3").chain_ref.unwrap();
    let mut vectors: Vec<Vec<u8>> = vec![
        good[..116].to_vec(),
        [good.clone(), vec![0]].concat(),
        vec![],
    ];
    for i in 0..good.len() {
        for bit in [0x01u8, 0x80] {
            let mut v = good.clone();
            v[i] ^= bit;
            vectors.push(v);
        }
    }
    let mut v = good.clone();
    v[85..117].copy_from_slice(&plus_q(&good[85..117].try_into().unwrap()).unwrap());
    vectors.push(v);
    for v in &vectors {
        let research = ResearchChainRef::decode(v)
            .map(|r| r.encode())
            .map_err(|e| format!("{e:?}"));
        let production = zwa_lineage::ChainRef::decode(v)
            .map(|r| r.encode().to_vec())
            .map_err(|e| format!("{e:?}"));
        assert_eq!(production, research, "{}", hex::encode(v));
    }
}

#[test]
fn rooted_mode_never_upgrades_a_design_a_result() {
    for case in cases() {
        let Ok(ev) = case.lineage.parse() else {
            continue;
        };
        let r = service().verify(VerificationMode::RootedLineage, &ev);
        assert!(!r.permits_trade_verification(), "{}", case.id);
    }
}
