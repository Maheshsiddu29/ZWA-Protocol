//! Shared fixture loading for the integration tests.
//!
//! Fixture bytes are the frozen research fixtures, copied byte for byte into
//! `tests/fixtures/` and pinned by sha256 in `tests/fixtures/fixtures.json`.
//! Every load re-checks the pin, so a modified fixture fails loudly instead of
//! silently changing what a test means.

#![allow(
    dead_code,
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing
)]

use std::path::PathBuf;
use std::sync::OnceLock;

use serde_json::Value;
use sha2::{Digest, Sha256};
use zwa_lineage::{
    ChainRef, FrozenStepVerifier, Phase1LineageService, RawLineageEvidence, RawStep,
    RootedLineageEvidence,
};

pub fn fixtures_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../tests/fixtures")
}

pub fn manifest() -> &'static Value {
    static M: OnceLock<Value> = OnceLock::new();
    M.get_or_init(|| {
        let text = std::fs::read_to_string(fixtures_dir().join("fixtures.json")).unwrap();
        serde_json::from_str(&text).unwrap()
    })
}

pub fn service() -> &'static Phase1LineageService {
    static S: OnceLock<Phase1LineageService> = OnceLock::new();
    S.get_or_init(|| Phase1LineageService::phase1(FrozenStepVerifier::load().unwrap()))
}

pub fn unhex(s: &str) -> Vec<u8> {
    hex::decode(s).unwrap()
}

pub fn unhex32(s: &str) -> [u8; 32] {
    unhex(s).try_into().unwrap()
}

/// One step of the fixture manifest: its public tuple and pinned proof bytes.
#[derive(Clone, Debug)]
pub struct FixtureStep {
    pub name: String,
    pub cmx_old: [u8; 32],
    pub nf_old: [u8; 32],
    pub cmx_new: [u8; 32],
    pub proof: Vec<u8>,
}

pub fn step(name: &str) -> FixtureStep {
    let s = &manifest()["steps"][name];
    assert!(s.is_object(), "unknown fixture step {name}");
    let proof = std::fs::read(
        fixtures_dir()
            .join("step-proofs")
            .join(format!("{name}.bin")),
    )
    .unwrap();
    assert_eq!(
        hex::encode(Sha256::digest(&proof)),
        s["proof_sha256"].as_str().unwrap(),
        "fixture {name} does not match its pinned sha256"
    );
    FixtureStep {
        name: name.to_string(),
        cmx_old: unhex32(s["cmx_old"].as_str().unwrap()),
        nf_old: unhex32(s["nf_old"].as_str().unwrap()),
        cmx_new: unhex32(s["cmx_new"].as_str().unwrap()),
        proof,
    }
}

/// A lineage of the manifest, as raw parts that tests may mutate.
#[derive(Clone, Debug)]
pub struct Lineage {
    pub chain_ref: Option<Vec<u8>>,
    pub root_cmx: Vec<u8>,
    pub terminal_cmx: Vec<u8>,
    pub steps: Vec<FixtureStep>,
}

pub fn chain_ref_bytes(v: &Value) -> Vec<u8> {
    let root =
        zwa_lineage::CanonicalOrchardRoot::from_slice(&unhex(v["orchard_root"].as_str().unwrap()))
            .unwrap();
    ChainRef::new(
        unhex32(v["network_id"].as_str().unwrap()),
        u32::try_from(v["height"].as_u64().unwrap()).unwrap(),
        unhex32(v["block_hash_internal"].as_str().unwrap()),
        root,
    )
    .unwrap()
    .encode()
    .to_vec()
}

pub fn lineage(name: &str) -> Lineage {
    let l = &manifest()["lineages"][name];
    assert!(l.is_object(), "unknown fixture lineage {name}");
    Lineage {
        chain_ref: l.get("chain_ref").map(chain_ref_bytes),
        root_cmx: unhex(l["root_cmx"].as_str().unwrap()),
        terminal_cmx: unhex(l["terminal_cmx"].as_str().unwrap()),
        steps: l["steps"]
            .as_array()
            .unwrap()
            .iter()
            .map(|s| step(s.as_str().unwrap()))
            .collect(),
    }
}

impl Lineage {
    pub fn raw(&self) -> RawLineageEvidence<'_> {
        RawLineageEvidence {
            chain_ref: self.chain_ref.as_deref(),
            root_cmx: &self.root_cmx,
            terminal_cmx: &self.terminal_cmx,
            steps: self
                .steps
                .iter()
                .map(|s| RawStep {
                    cmx_old: &s.cmx_old,
                    nf_old: &s.nf_old,
                    cmx_new: &s.cmx_new,
                    proof: &s.proof,
                })
                .collect(),
        }
    }

    pub fn parse(&self) -> Result<RootedLineageEvidence, zwa_lineage::VerificationFailure> {
        RootedLineageEvidence::parse(&self.raw())
    }

    pub fn evidence(&self) -> RootedLineageEvidence {
        self.parse().unwrap()
    }

    /// The version-2 wire encoding of this lineage.
    pub fn request_bytes(&self) -> Vec<u8> {
        zwa_lineage::encode_request(&self.evidence()).unwrap()
    }
}

/// `q_P` (Pallas base modulus) little-endian, for non-canonical encodings.
pub const Q_P_LE: [u8; 32] = {
    let be: [u8; 32] = [
        0x40, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x22, 0x46, 0x98, 0xfc, 0x09, 0x4c,
        0xf9, 0x1b, 0x99, 0x2d, 0x30, 0xed, 0x00, 0x00, 0x00, 0x01,
    ];
    let mut le = [0u8; 32];
    let mut i = 0;
    while i < 32 {
        le[i] = be[31 - i];
        i += 1;
    }
    le
};

/// `x + q_P` as 32 LE bytes: the non-canonical alias of a canonical `x`, as in
/// research rows CB-15i / CB-15j. `None` if it overflows 256 bits.
pub fn plus_q(x: &[u8; 32]) -> Option<[u8; 32]> {
    let mut out = [0u8; 32];
    let mut carry = 0u16;
    for i in 0..32 {
        let s = u16::from(x[i]) + u16::from(Q_P_LE[i]) + carry;
        out[i] = (s & 0xff) as u8;
        carry = s >> 8;
    }
    (carry == 0).then_some(out)
}
