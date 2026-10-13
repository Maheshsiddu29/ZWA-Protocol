//! Failure codes, the ZWA provenance policy (`ROOT_AUTH_SPEC.md` §4) and the
//! verifier configuration. Everything here is verifier-side.

use std::fmt;

use crate::chainref::NetworkKind;
use crate::util::{blake2b_256, H32};

/// Every Layer-B rejection. Names are the spec's codes.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Code {
    // chain_ref (CHAINREF_SPEC §4)
    Malformed,
    UnsupportedVersion,
    ViewUnavailable,
    Network,
    UnknownFuture,
    Unknown,
    Invalidated,
    FinalityViolation,
    TreestateMismatch,
    NotFinal,
    CheckpointConflict,
    Expired,
    CheckpointedHistory,
    // root (ROOT_AUTH_SPEC §3–§4)
    RootMalformed,
    RootOutOfRange,
    RootNotIssuance,
    RootContext,
    RootCmx,
    RootMerkle,
    RootIssuanceInvalid,
    RootAssetMismatch,
    RootShape,
    RootPolicyUntrusted,
    RootPolicyRevoked,
    RootPolicyNetwork,
    RootPolicyIssuer,
    RootPolicyAsset,
    RootPolicyEntryRevoked,
    RootPolicyWindow,
    // terminal (TERMINAL_MAF_SPEC §3)
    TermMalformed,
    TermOutOfRange,
    TermNotAction,
    TermNotZsaAction,
    TermCmx,
    TermMerkle,
    TermActionProof,
    TermAnchor,
    // Design A (AcceptDA) and context
    DaEmpty,
    DaNonCanonical(usize),
    DaProof(usize),
    DaJoin(usize),
    DaRoot,
    DaTerminal,
    DaVkIdentity,
    Context,
}

impl fmt::Display for Code {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let s = match self {
            Code::Malformed => "MALFORMED".to_string(),
            Code::UnsupportedVersion => "UNSUPPORTED_VERSION".into(),
            Code::ViewUnavailable => "VIEW_UNAVAILABLE".into(),
            Code::Network => "NETWORK".into(),
            Code::UnknownFuture => "UNKNOWN_FUTURE".into(),
            Code::Unknown => "UNKNOWN".into(),
            Code::Invalidated => "INVALIDATED".into(),
            Code::FinalityViolation => "FINALITY_VIOLATION".into(),
            Code::TreestateMismatch => "TREESTATE_MISMATCH".into(),
            Code::NotFinal => "NOT_FINAL".into(),
            Code::CheckpointConflict => "CHECKPOINT_CONFLICT".into(),
            Code::Expired => "EXPIRED".into(),
            Code::CheckpointedHistory => "CHECKPOINTED_HISTORY".into(),
            Code::RootMalformed => "ROOT_MALFORMED".into(),
            Code::RootOutOfRange => "ROOT_OUT_OF_RANGE".into(),
            Code::RootNotIssuance => "ROOT_NOT_ISSUANCE".into(),
            Code::RootContext => "ROOT_CONTEXT".into(),
            Code::RootCmx => "ROOT_CMX".into(),
            Code::RootMerkle => "ROOT_MERKLE".into(),
            Code::RootIssuanceInvalid => "ROOT_ISSUANCE_INVALID".into(),
            Code::RootAssetMismatch => "ROOT_ASSET_MISMATCH".into(),
            Code::RootShape => "ROOT_SHAPE".into(),
            Code::RootPolicyUntrusted => "ROOT_POLICY_UNTRUSTED".into(),
            Code::RootPolicyRevoked => "ROOT_POLICY_REVOKED".into(),
            Code::RootPolicyNetwork => "ROOT_POLICY_NETWORK".into(),
            Code::RootPolicyIssuer => "ROOT_POLICY_ISSUER".into(),
            Code::RootPolicyAsset => "ROOT_POLICY_ASSET".into(),
            Code::RootPolicyEntryRevoked => "ROOT_POLICY_ENTRY_REVOKED".into(),
            Code::RootPolicyWindow => "ROOT_POLICY_WINDOW".into(),
            Code::TermMalformed => "TERM_MALFORMED".into(),
            Code::TermOutOfRange => "TERM_OUT_OF_RANGE".into(),
            Code::TermNotAction => "TERM_NOT_ACTION".into(),
            Code::TermNotZsaAction => "TERM_NOT_ZSA_ACTION".into(),
            Code::TermCmx => "TERM_CMX".into(),
            Code::TermMerkle => "TERM_MERKLE".into(),
            Code::TermActionProof => "TERM_ACTION_PROOF".into(),
            Code::TermAnchor => "TERM_ANCHOR".into(),
            Code::DaEmpty => "DA_EMPTY".into(),
            Code::DaNonCanonical(i) => format!("DA_NONCANONICAL({i})"),
            Code::DaProof(i) => format!("DA_PROOF({i})"),
            Code::DaJoin(i) => format!("DA_JOIN({i})"),
            Code::DaRoot => "DA_ROOT".into(),
            Code::DaTerminal => "DA_TERMINAL".into(),
            Code::DaVkIdentity => "DA_VK_IDENTITY".into(),
            Code::Context => "CONTEXT".into(),
        };
        f.write_str(&s)
    }
}

// ---- Provenance policy --------------------------------------------------------

pub const POLICY_TAG: &[u8; 16] = b"ZWA-LB-POLICY:v1";
pub const STATUS_ACTIVE: u8 = 1;
pub const STATUS_REVOKED: u8 = 2;
pub const OPEN_END: u32 = u32::MAX;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PolicyEntry {
    pub issuer_ik: [u8; 33],
    pub asset_desc_hash: H32,
    pub asset_base: H32,
    pub valid_from: u32,
    pub valid_until: u32,
    pub status: u8,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProvenancePolicy {
    pub policy_id: H32,
    pub network_id: H32,
    pub entries: Vec<PolicyEntry>,
}

impl ProvenancePolicy {
    pub fn encode(&self) -> Vec<u8> {
        let mut v = POLICY_TAG.to_vec();
        v.push(1);
        v.extend_from_slice(&self.policy_id);
        v.extend_from_slice(&self.network_id);
        v.extend_from_slice(&(self.entries.len() as u16).to_le_bytes());
        for e in &self.entries {
            v.extend_from_slice(&e.issuer_ik);
            v.extend_from_slice(&e.asset_desc_hash);
            v.extend_from_slice(&e.asset_base);
            v.extend_from_slice(&e.valid_from.to_le_bytes());
            v.extend_from_slice(&e.valid_until.to_le_bytes());
            v.push(e.status);
        }
        v
    }

    pub fn decode(b: &[u8]) -> Option<Self> {
        if b.len() < 16 + 1 + 64 + 2 || &b[..16] != POLICY_TAG || b[16] != 1 {
            return None;
        }
        let policy_id: H32 = b[17..49].try_into().ok()?;
        let network_id: H32 = b[49..81].try_into().ok()?;
        let n = u16::from_le_bytes(b[81..83].try_into().ok()?) as usize;
        const E: usize = 33 + 32 + 32 + 4 + 4 + 1;
        if b.len() != 83 + n * E {
            return None;
        }
        let mut entries = vec![];
        for i in 0..n {
            let e = &b[83 + i * E..83 + (i + 1) * E];
            let status = e[105];
            if status != STATUS_ACTIVE && status != STATUS_REVOKED {
                return None;
            }
            entries.push(PolicyEntry {
                issuer_ik: e[..33].try_into().ok()?,
                asset_desc_hash: e[33..65].try_into().ok()?,
                asset_base: e[65..97].try_into().ok()?,
                valid_from: u32::from_le_bytes(e[97..101].try_into().ok()?),
                valid_until: u32::from_le_bytes(e[101..105].try_into().ok()?),
                status,
            });
        }
        Some(ProvenancePolicy { policy_id, network_id, entries })
    }

    pub fn digest(&self) -> H32 {
        blake2b_256(b"ZWA_LB_PolicyDig", &self.encode())
    }
}

// ---- Verifier configuration ----------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FinalityMode {
    Depth(u32),
    Checkpoint,
    Hybrid(u32),
}

/// Everything the verifier decides for itself. Nothing here comes from the prover.
#[derive(Clone, Debug)]
pub struct VerifierConfig {
    pub kind: NetworkKind,
    pub action_vk_id: H32,
    pub ruleset_tag: String,
    pub allowed_networks: Vec<H32>,
    /// Encoded provenance policy as loaded by the verifier (None = no policy).
    pub policy_bytes: Option<Vec<u8>>,
    pub accepted_policy_digests: Vec<H32>,
    pub revoked_policy_digests: Vec<H32>,
    pub finality: FinalityMode,
    /// ZWA-trusted checkpoints (height, internal-order hash).
    pub trusted_checkpoints: Vec<(u32, H32)>,
    pub max_age: Option<u32>,
    pub accept_checkpointed_history: bool,
}
