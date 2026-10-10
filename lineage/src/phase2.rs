//! Snapshot-backed Layer B adapter for the provisional engineering baseline.
//!
//! The snapshot bytes and their SHA-256 pins are owned by this crate. The
//! frozen Layer-B implementation re-derives the block, transaction and
//! Orchard-tree facts at load. No caller supplies a view or policy through a
//! lineage request. The sibling research crate remains a pinned source and
//! runtime dependency until the exact source can be packaged locally.

use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::{Arc, Mutex};
use std::time::Instant;

use orchard::circuit::VerifyingKey;
use sha2::{Digest, Sha256};
use zwa_layer_b_core::chainref::{self as research_ref, NetworkKind, ACTION_VK_ID, RULESET_TAG};
use zwa_layer_b_core::finality::{self as research_finality, Tracker};
use zwa_layer_b_core::policy::{Code, FinalityMode, ProvenancePolicy, VerifierConfig};
use zwa_layer_b_core::root::{self as research_root, ROOT_EVIDENCE_LEN};
use zwa_layer_b_core::terminal::{self as research_terminal, TERMINAL_EVIDENCE_LEN};
use zwa_layer_b_core::view::ChainView as ResearchView;

use crate::auth::{
    AuthenticatedChainContext, AuthenticatedRoot, AuthenticatedTerminal, ChainContextAuthenticator,
    ChainIdentityResult, RootAuthenticationResult, RootAuthenticator, TerminalAuthenticationResult,
    TerminalAuthenticator,
};
use crate::chain_ref::ChainRef;
use crate::container::{decode_request, encode_request, REQUEST_TAG};
use crate::evidence::RootedLineageEvidence;
use crate::field::CanonicalCmx;
use crate::finality::{FinalityResult, FinalizedReference, HistoryMode};
use crate::frozen::FrozenStepVerifier;
use crate::service::{
    LineageResult, LineageVerificationService, ObligationStatus, Obligations, VerificationEvidence,
    VerificationMode,
};
use crate::verdict::{FailureCode, VerificationFailure};

/// The selected, sha256-pinned snapshot. These IDs are configuration, never
/// values accepted from the proof container.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SnapshotId {
    /// A, tip 4: reference height 4 is pending finality.
    A4,
    /// A, tip 7: reference height 4 is depth-3 finalized.
    A7,
    /// A with checkpoint-committed heights 1–4.
    Cpa,
    /// Branch B after the natural research reorganization.
    B8,
    /// CKN checkpoint counterexample view.
    Ckn,
}

impl SnapshotId {
    /// Stable snapshot name.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::A4 => "A4",
            Self::A7 => "A7",
            Self::Cpa => "CPA",
            Self::B8 => "B8",
            Self::Ckn => "CKN",
        }
    }

    /// Frozen bytes copied from the authoritative research run.
    pub const fn bytes(self) -> &'static [u8] {
        match self {
            Self::A4 => include_bytes!("../snapshots/A4.json"),
            Self::A7 => include_bytes!("../snapshots/A7.json"),
            Self::Cpa => include_bytes!("../snapshots/CPA.json"),
            Self::B8 => include_bytes!("../snapshots/B8.json"),
            Self::Ckn => include_bytes!("../snapshots/CKN.json"),
        }
    }

    /// SHA-256 pinned in production configuration.
    pub const fn sha256(self) -> &'static str {
        match self {
            Self::A4 => "b1cf498742737f15a30af75c30a0a12e0701257a1c6a2e891902c42acc6f3cde",
            Self::A7 => "28651a629cf0d8075c5d0ac7a7f93db45dd10729e5ba0dc48c156f903f3b8300",
            Self::Cpa => "8331468eedc194da5456a5f059db9dbdc0ae6f511efcc5180a9d7d410ceaf02e",
            Self::B8 => "63f671fffdddb844aef3ac935ee56d901e9eaba6f82df04f361f356b68b7adcf",
            Self::Ckn => "3466f0ad69d62559d7ca240ecab88fc8e73e8a24062ed89f098eeabbb5efe232",
        }
    }
}

/// Validated, immutable verifier-owned snapshot. The research view is private
/// to this module and is only constructed after checking the outer hash and
/// re-computing all Layer-B integrity properties.
pub struct SnapshotChainView {
    id: SnapshotId,
    view: ResearchView,
}

impl SnapshotChainView {
    /// Loads a pinned snapshot. Any hash or semantic mismatch stops startup.
    ///
    /// # Errors
    /// A corrupted snapshot, failed semantic validation, or loader panic.
    pub fn load(id: SnapshotId) -> Result<Self, String> {
        Self::load_bytes(id, id.bytes())
    }

    /// Validates supplied bytes against the compiled pin, useful for an
    /// integrity check before a snapshot is deployed.
    ///
    /// # Errors
    /// A hash mismatch, semantic mismatch or loader panic.
    pub fn load_bytes(id: SnapshotId, bytes: &[u8]) -> Result<Self, String> {
        if hex::encode(Sha256::digest(bytes)) != id.sha256() {
            return Err("snapshot SHA-256 mismatch".into());
        }
        let action_vk = hex::decode(ACTION_VK_ID).map_err(|_| "invalid pinned Action VK")?;
        let action_vk: [u8; 32] = action_vk
            .try_into()
            .map_err(|_| "invalid Action VK length")?;
        let loaded = catch_unwind(AssertUnwindSafe(|| {
            ResearchView::load(bytes, NetworkKind::Regtest, action_vk, RULESET_TAG)
        }))
        .map_err(|_| "snapshot validation panicked")??;
        if loaded.label != id.as_str() {
            return Err("snapshot identity mismatch".into());
        }
        Ok(Self { id, view: loaded })
    }

    /// Snapshot identity included in public verification evidence.
    pub const fn id(&self) -> SnapshotId {
        self.id
    }

    /// Verifier-computed network identity.
    pub const fn network_id(&self) -> [u8; 32] {
        self.view.network_id
    }

    /// Current accepted tip in this snapshot.
    pub const fn tip(&self) -> u32 {
        self.view.tip
    }
}

/// Strict production container v3. Layout: v2 prefix through terminal_cmx,
/// fixed 1,169-byte root evidence, fixed 1,028-byte terminal evidence, then
/// v2 step-count and steps. The operator-owned history mode is not serialized.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RootedRequestV3 {
    /// Canonical Design-A and chain-ref evidence.
    pub evidence: RootedLineageEvidence,
    /// Root evidence in the frozen R1–R8 encoding.
    pub root_evidence: [u8; ROOT_EVIDENCE_LEN],
    /// Terminal evidence in the frozen T1–T7 encoding.
    pub terminal_evidence: [u8; TERMINAL_EVIDENCE_LEN],
}

const V3: u8 = 3;
const PREFIX: usize = 16 + 1 + 32 + 117 + 32 + 32;

impl RootedRequestV3 {
    /// Encodes a canonical request with no alternate field ordering.
    ///
    /// # Errors
    /// Missing chain reference or invalid step count.
    pub fn encode(&self) -> Result<Vec<u8>, VerificationFailure> {
        let v2 = encode_request(&self.evidence)?;
        let mut out = Vec::with_capacity(v2.len() + ROOT_EVIDENCE_LEN + TERMINAL_EVIDENCE_LEN);
        out.extend_from_slice(v2.get(..PREFIX).ok_or_else(malformed)?);
        if let Some(version) = out.get_mut(REQUEST_TAG.len()) {
            *version = V3;
        }
        out.extend_from_slice(&self.root_evidence);
        out.extend_from_slice(&self.terminal_evidence);
        out.extend_from_slice(v2.get(PREFIX..).ok_or_else(malformed)?);
        Ok(out)
    }

    /// Strictly decodes v3; delegates canonical tuples, proof lengths, VK and
    /// chain-ref parsing to the unchanged v2 parser.
    ///
    /// # Errors
    /// Malformed fields, unknown version, trailing data or a foreign VK.
    pub fn decode(bytes: &[u8]) -> Result<Self, VerificationFailure> {
        if bytes.get(..REQUEST_TAG.len()) != Some(REQUEST_TAG.as_slice()) {
            return Err(malformed());
        }
        if bytes.get(REQUEST_TAG.len()) != Some(&V3) {
            return Err(VerificationFailure::new(FailureCode::UnsupportedVersion));
        }
        let evidence_end = PREFIX
            .checked_add(ROOT_EVIDENCE_LEN + TERMINAL_EVIDENCE_LEN)
            .ok_or_else(malformed)?;
        let root: [u8; ROOT_EVIDENCE_LEN] = bytes
            .get(PREFIX..PREFIX + ROOT_EVIDENCE_LEN)
            .ok_or_else(malformed)?
            .try_into()
            .map_err(|_| malformed())?;
        let terminal: [u8; TERMINAL_EVIDENCE_LEN] = bytes
            .get(PREFIX + ROOT_EVIDENCE_LEN..evidence_end)
            .ok_or_else(malformed)?
            .try_into()
            .map_err(|_| malformed())?;
        let mut v2 = Vec::with_capacity(bytes.len() - ROOT_EVIDENCE_LEN - TERMINAL_EVIDENCE_LEN);
        v2.extend_from_slice(bytes.get(..PREFIX).ok_or_else(malformed)?);
        if let Some(version) = v2.get_mut(REQUEST_TAG.len()) {
            *version = 2;
        }
        v2.extend_from_slice(bytes.get(evidence_end..).ok_or_else(malformed)?);
        let request = decode_request(&v2)?;
        Ok(Self {
            evidence: request.evidence,
            root_evidence: root,
            terminal_evidence: terminal,
        })
    }
}

fn malformed() -> VerificationFailure {
    VerificationFailure::new(FailureCode::InvalidEncoding)
}

fn research_code(code: Code) -> (&'static str, FailureCode) {
    use Code::*;
    match code {
        Malformed => ("MALFORMED", FailureCode::ChainRefMalformed),
        UnsupportedVersion => (
            "UNSUPPORTED_VERSION",
            FailureCode::ChainRefUnsupportedVersion,
        ),
        ViewUnavailable => ("VIEW_UNAVAILABLE", FailureCode::AuthenticationUnavailable),
        Network => ("NETWORK", FailureCode::ChainContextRejected),
        UnknownFuture => ("UNKNOWN_FUTURE", FailureCode::ChainContextRejected),
        Unknown => ("UNKNOWN", FailureCode::ChainContextRejected),
        Invalidated => ("INVALIDATED", FailureCode::ReferenceInvalidated),
        FinalityViolation => ("FINALITY_VIOLATION", FailureCode::FinalityViolation),
        TreestateMismatch => ("TREESTATE_MISMATCH", FailureCode::ChainContextRejected),
        NotFinal => ("NOT_FINAL", FailureCode::ReferenceNotFinal),
        CheckpointConflict => ("CHECKPOINT_CONFLICT", FailureCode::InternalVerifierError),
        Expired => ("EXPIRED", FailureCode::ChainContextRejected),
        CheckpointedHistory => ("CHECKPOINTED_HISTORY", FailureCode::CheckpointedHistory),
        RootMalformed => ("ROOT_MALFORMED", FailureCode::RootAuthenticationFailed),
        RootOutOfRange => ("ROOT_OUT_OF_RANGE", FailureCode::RootAuthenticationFailed),
        RootNotIssuance => ("ROOT_NOT_ISSUANCE", FailureCode::RootAuthenticationFailed),
        RootContext => ("ROOT_CONTEXT", FailureCode::RootAuthenticationFailed),
        RootCmx => ("ROOT_CMX", FailureCode::RootAuthenticationFailed),
        RootMerkle => ("ROOT_MERKLE", FailureCode::RootAuthenticationFailed),
        RootIssuanceInvalid => (
            "ROOT_ISSUANCE_INVALID",
            FailureCode::RootAuthenticationFailed,
        ),
        RootAssetMismatch => ("ROOT_ASSET_MISMATCH", FailureCode::RootAuthenticationFailed),
        RootShape => ("ROOT_SHAPE", FailureCode::RootAuthenticationFailed),
        RootPolicyUntrusted => (
            "ROOT_POLICY_UNTRUSTED",
            FailureCode::RootAuthenticationFailed,
        ),
        RootPolicyRevoked => ("ROOT_POLICY_REVOKED", FailureCode::RootAuthenticationFailed),
        RootPolicyNetwork => ("ROOT_POLICY_NETWORK", FailureCode::RootAuthenticationFailed),
        RootPolicyIssuer => ("ROOT_POLICY_ISSUER", FailureCode::RootAuthenticationFailed),
        RootPolicyAsset => ("ROOT_POLICY_ASSET", FailureCode::RootAuthenticationFailed),
        RootPolicyEntryRevoked => (
            "ROOT_POLICY_ENTRY_REVOKED",
            FailureCode::RootAuthenticationFailed,
        ),
        RootPolicyWindow => ("ROOT_POLICY_WINDOW", FailureCode::RootAuthenticationFailed),
        TermMalformed => ("TERM_MALFORMED", FailureCode::TerminalAuthenticationFailed),
        TermOutOfRange => (
            "TERM_OUT_OF_RANGE",
            FailureCode::TerminalAuthenticationFailed,
        ),
        TermNotAction => ("TERM_NOT_ACTION", FailureCode::TerminalAuthenticationFailed),
        TermNotZsaAction => ("TERM_NOT_ZSA_ACTION", FailureCode::TerminalUnsupported),
        TermCmx => ("TERM_CMX", FailureCode::TerminalAuthenticationFailed),
        TermMerkle => ("TERM_MERKLE", FailureCode::TerminalAuthenticationFailed),
        TermActionProof => (
            "TERM_ACTION_PROOF",
            FailureCode::TerminalAuthenticationFailed,
        ),
        TermAnchor => ("TERM_ANCHOR", FailureCode::TerminalAuthenticationFailed),
        _ => ("INTERNAL_LAYER_B_CODE", FailureCode::InternalVerifierError),
    }
}

fn failure(code: Code) -> VerificationFailure {
    let (name, category) = research_code(code);
    VerificationFailure::layer_b(category, name)
}

fn research_ref(r: &ChainRef) -> Result<research_ref::ChainRef, VerificationFailure> {
    research_ref::ChainRef::decode(&r.encode()).map_err(|_| malformed())
}

struct ChainAdapter {
    view: Arc<SnapshotChainView>,
    config: Arc<VerifierConfig>,
    tracker: Arc<Mutex<Tracker>>,
}

impl ChainContextAuthenticator for ChainAdapter {
    fn authenticate_chain_ref(&self, r: &ChainRef) -> ChainIdentityResult {
        let tracker = match self.tracker.lock() {
            Ok(t) => t,
            Err(_) => {
                return ChainIdentityResult::InternalError(VerificationFailure::new(
                    FailureCode::InternalVerifierError,
                ))
            }
        };
        match research_finality::validate_identity(
            &r.encode(),
            Some(&self.view.view),
            &self.config,
            &tracker,
        ) {
            Ok(_) => {
                let mode = if self.view.view.node_checkpoint_max == 0 {
                    HistoryMode::FullValidation
                } else {
                    HistoryMode::CheckpointTrust {
                        checkpoint_height: self.view.view.node_checkpoint_max,
                    }
                };
                ChainIdentityResult::Authenticated(AuthenticatedChainContext::new(
                    *r,
                    self.view.tip(),
                    mode,
                ))
            }
            Err(Code::Invalidated) => ChainIdentityResult::Invalidated {
                finality_violation: false,
            },
            Err(Code::FinalityViolation) => ChainIdentityResult::Invalidated {
                finality_violation: true,
            },
            Err(e) => {
                let f = failure(e);
                if f.status() == crate::verdict::LineageStatus::InternalError {
                    ChainIdentityResult::InternalError(f)
                } else {
                    ChainIdentityResult::Rejected(f)
                }
            }
        }
    }

    fn evaluate_finality(&self, c: &AuthenticatedChainContext) -> FinalityResult {
        let r = match research_ref(c.chain_ref()) {
            Ok(r) => r,
            Err(f) => return FinalityResult::InternalError(f),
        };
        let mut tracker = match self.tracker.lock() {
            Ok(t) => t,
            Err(_) => {
                return FinalityResult::InternalError(VerificationFailure::new(
                    FailureCode::InternalVerifierError,
                ))
            }
        };
        match research_finality::validate_policy(&r, &self.view.view, &self.config, &mut tracker) {
            Ok(_) => FinalityResult::Finalized(FinalizedReference::new(
                c.history_mode(),
                self.view.tip() - r.height,
            )),
            Err(Code::NotFinal) => FinalityResult::NotFinal,
            Err(Code::CheckpointedHistory) => FinalityResult::CheckpointTrustRequired,
            Err(Code::Invalidated) => FinalityResult::Invalidated {
                finality_violation: false,
            },
            Err(Code::FinalityViolation) => FinalityResult::Invalidated {
                finality_violation: true,
            },
            Err(e) => {
                let f = failure(e);
                if f.status() == crate::verdict::LineageStatus::InternalError {
                    FinalityResult::InternalError(f)
                } else {
                    FinalityResult::Rejected(f)
                }
            }
        }
    }
}

struct RootAdapter {
    view: Arc<SnapshotChainView>,
    config: Arc<VerifierConfig>,
    evidence: [u8; ROOT_EVIDENCE_LEN],
}

impl RootAuthenticator for RootAdapter {
    fn authenticate_root(
        &self,
        cmx: &CanonicalCmx,
        c: &AuthenticatedChainContext,
    ) -> RootAuthenticationResult {
        let r = match research_ref(c.chain_ref()) {
            Ok(r) => r,
            Err(f) => return RootAuthenticationResult::InternalError(f),
        };
        match research_root::authorize_root(
            cmx.as_bytes(),
            &self.evidence,
            &r,
            &self.view.view,
            &self.config,
        ) {
            Ok(root) => RootAuthenticationResult::Authenticated(AuthenticatedRoot::new(
                root.height,
                root.position,
                root.asset_base,
            )),
            Err(e) => RootAuthenticationResult::Rejected(failure(e)),
        }
    }
}

struct TerminalAdapter {
    view: Arc<SnapshotChainView>,
    evidence: [u8; TERMINAL_EVIDENCE_LEN],
    action_vk: Arc<VerifyingKey>,
}

impl TerminalAuthenticator for TerminalAdapter {
    fn authenticate_terminal(
        &self,
        cmx: &CanonicalCmx,
        c: &AuthenticatedChainContext,
    ) -> TerminalAuthenticationResult {
        let r = match research_ref(c.chain_ref()) {
            Ok(r) => r,
            Err(f) => return TerminalAuthenticationResult::InternalError(f),
        };
        match research_terminal::authenticate_terminal(
            cmx.as_bytes(),
            &self.evidence,
            &r,
            &self.view.view,
            &self.action_vk,
        ) {
            Ok(term) => TerminalAuthenticationResult::Authenticated(AuthenticatedTerminal::new(
                term.height,
                term.position,
            )),
            Err(e) => TerminalAuthenticationResult::Rejected(failure(e)),
        }
    }
}

/// Operator-owned policy and current view for a rooted verification service.
/// The tracker survives view changes while this object stays alive.
pub struct Phase2LineageVerifier {
    verifier: FrozenStepVerifier,
    view: Arc<SnapshotChainView>,
    config: Arc<VerifierConfig>,
    tracker: Arc<Mutex<Tracker>>,
    action_vk: Arc<VerifyingKey>,
}

/// Deterministic application-facing trade and settlement permission. Only a
/// fully rooted `VERIFIED` lineage may allow execution.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TradePermission {
    /// Whether a matcher may advance the trade.
    pub allowed: bool,
    /// The exact lineage status that produced this decision.
    pub reason: &'static str,
    /// Public statement identifier, when canonical parsing reached it.
    pub lineage_statement_id: Option<[u8; 32]>,
}

impl TradePermission {
    /// Converts one verifier result to a fail-closed matcher decision.
    pub fn from_result(result: &LineageResult) -> Self {
        Self {
            allowed: result.permits_trade_verification(),
            reason: result.status.as_str(),
            lineage_statement_id: result.evidence.lineage_statement_id,
        }
    }
}

/// Verifier-owned Layer-B policy. The public provenance policy bytes and their
/// accepted digest are supplied by the operator, never by a v3 request.
#[derive(Clone)]
pub struct Phase2VerifierConfig {
    network_id: [u8; 32],
    policy_bytes: Vec<u8>,
    policy_digest: [u8; 32],
    trusted_checkpoints: Vec<(u32, [u8; 32])>,
    max_age: Option<u32>,
    accept_checkpointed_history: bool,
}

impl Phase2VerifierConfig {
    /// Establishes the pinned public provenance policy for one network.
    /// Checkpoint trust is off by default; finality uses research K = 3.
    pub fn new(network_id: [u8; 32], policy_bytes: Vec<u8>, policy_digest: [u8; 32]) -> Self {
        Self {
            network_id,
            policy_bytes,
            policy_digest,
            trusted_checkpoints: Vec::new(),
            max_age: None,
            accept_checkpointed_history: false,
        }
    }

    /// Records an explicit operator CKPT-R trust decision for checkpointed
    /// history. The caller must validate the checkpoint list's C1–C3 premises.
    pub fn trust_checkpoint_history(mut self) -> Self {
        self.accept_checkpointed_history = true;
        self
    }

    /// Adds a ZWA-trusted checkpoint (height, internal-order block hash).
    pub fn with_trusted_checkpoint(mut self, height: u32, hash: [u8; 32]) -> Self {
        self.trusted_checkpoints.push((height, hash));
        self
    }

    /// Sets an optional maximum age in blocks for a chain reference.
    pub fn with_max_age(mut self, max_age: u32) -> Self {
        self.max_age = Some(max_age);
        self
    }

    fn research_config(&self, view: &SnapshotChainView) -> Result<VerifierConfig, String> {
        if view.id() == SnapshotId::Ckn && self.accept_checkpointed_history {
            return Err("CKN is a recorded violation of CKPT-R".into());
        }
        if self.network_id != view.network_id() {
            return Err("network configuration does not allow this view".into());
        }
        let policy = ProvenancePolicy::decode(&self.policy_bytes)
            .ok_or("invalid provenance policy encoding")?;
        if policy.network_id != self.network_id || policy.digest() != self.policy_digest {
            return Err("provenance policy digest or network mismatch".into());
        }
        let action_vk_id: [u8; 32] = hex::decode(ACTION_VK_ID)
            .map_err(|_| "invalid pinned Action VK")?
            .try_into()
            .map_err(|_| "invalid Action VK length")?;
        Ok(VerifierConfig {
            kind: NetworkKind::Regtest,
            action_vk_id,
            ruleset_tag: RULESET_TAG.to_string(),
            allowed_networks: vec![self.network_id],
            policy_bytes: Some(self.policy_bytes.clone()),
            accepted_policy_digests: vec![self.policy_digest],
            revoked_policy_digests: vec![],
            finality: FinalityMode::Depth(3),
            trusted_checkpoints: self.trusted_checkpoints.clone(),
            max_age: self.max_age,
            accept_checkpointed_history: self.accept_checkpointed_history,
        })
    }
}

impl Phase2LineageVerifier {
    /// Builds the service after checking its network and policy pins.
    ///
    /// # Errors
    /// Frozen verifier identity or operator configuration mismatch.
    pub fn new(view: SnapshotChainView, config: Phase2VerifierConfig) -> Result<Self, String> {
        let config = config.research_config(&view)?;
        let verifier = FrozenStepVerifier::load().map_err(|e| format!("frozen verifier: {e}"))?;
        let action_vk = catch_unwind(VerifyingKey::build::<orchard::flavor::OrchardZSA>)
            .map_err(|_| "Action verifying key derivation panicked")?;
        Ok(Self {
            verifier,
            view: Arc::new(view),
            config: Arc::new(config),
            tracker: Arc::new(Mutex::new(Tracker::default())),
            action_vk: Arc::new(action_vk),
        })
    }

    /// Replaces the accepted view while retaining observed reference states.
    /// The caller must supply another independently pinned and validated view.
    pub fn set_view(&mut self, view: SnapshotChainView) -> Result<(), String> {
        if view.network_id() != self.view.network_id() {
            return Err("network identity changed".into());
        }
        if view.id() == SnapshotId::Ckn && self.config.accept_checkpointed_history {
            return Err("CKN is a recorded violation of CKPT-R".into());
        }
        self.view = Arc::new(view);
        Ok(())
    }

    /// Verifies a strict v3 request against the current view. All non-VERIFIED
    /// results deny trade permission.
    pub fn verify_v3(&self, bytes: &[u8]) -> LineageResult {
        let start = Instant::now();
        let request = match RootedRequestV3::decode(bytes) {
            Ok(r) => r,
            Err(f) => {
                let mut obligations = Obligations::none();
                obligations.parse = ObligationStatus::Failed;
                let mut evidence = VerificationEvidence::new(*self.verifier.identity());
                evidence.snapshot_id = Some(self.view.id().as_str());
                evidence.snapshot_sha256 = Some(self.view.id().sha256());
                evidence.decode_time = Some(start.elapsed());
                evidence.total_time = start.elapsed();
                return LineageResult {
                    mode: VerificationMode::RootedLineage,
                    status: f.status(),
                    failure: Some(f),
                    obligations,
                    evidence,
                };
            }
        };
        let decode_time = start.elapsed();
        let service = LineageVerificationService::new(
            self.verifier.clone(),
            ChainAdapter {
                view: Arc::clone(&self.view),
                config: Arc::clone(&self.config),
                tracker: Arc::clone(&self.tracker),
            },
            RootAdapter {
                view: Arc::clone(&self.view),
                config: Arc::clone(&self.config),
                evidence: request.root_evidence,
            },
            TerminalAdapter {
                view: Arc::clone(&self.view),
                evidence: request.terminal_evidence,
                action_vk: Arc::clone(&self.action_vk),
            },
        );
        let mut result = service.verify_rooted_lineage(&request.evidence);
        result.evidence.snapshot_id = Some(self.view.id().as_str());
        result.evidence.snapshot_sha256 = Some(self.view.id().sha256());
        result.evidence.decode_time = Some(decode_time);
        result.evidence.total_time = start.elapsed();
        result
    }
}
