//! Lineage verification service (contract §C11, §H, M9).
//!
//! ```text
//! verify_design_a       cryptographic lineage verification   → LINEAGE_PROOF_VALID | REJECTED | INTERNAL_ERROR
//! verify_chain_context  Layer-B chain_ref authentication
//! verify_root           Layer-B root authentication
//! verify_terminal       Layer-B terminal authentication
//! verify_rooted_lineage orchestration over all of them        → VERIFIED only if every obligation passed
//! ```
//!
//! Rooted orchestration follows the contract's acceptance order: chain_ref
//! identity → root → Design A → terminal → context (A5) → finality/policy. A
//! definitive failure (`REJECTED`, `UNSUPPORTED`, `INVALIDATED_BY_REORG`,
//! `CHECKPOINT_TRUST_REQUIRED`, `INTERNAL_ERROR`) ends the run immediately.
//!
//! A dependency that is not wired in this build (`Unavailable(NotWired)`)
//! leaves its obligation `PENDING`, and every obligation that needs its output
//! `NOT_EVALUATED`; evaluation continues with the obligations that do not
//! depend on it, so a false proof is still `REJECTED`. If nothing failed but
//! something is pending, the result is `PENDING_AUTHENTICATION`. `VERIFIED`
//! requires every obligation `PASSED`. A wired dependency that fails at run
//! time (view, RPC, timeout, aborted node) is `INTERNAL_ERROR` (§F1, §F2).
//!
//! Every adapter call runs under `catch_unwind`: a panic is `INTERNAL_ERROR`.

use core::fmt;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::time::{Duration, Instant};

use serde_json::{json, Value};

use crate::auth::{
    AuthenticatedChainContext, ChainContextAuthenticator, ChainIdentityResult,
    NotWiredChainContext, NotWiredRootAuthenticator, NotWiredTerminalAuthenticator,
    RootAuthenticationResult, RootAuthenticator, TerminalAuthenticationResult,
    TerminalAuthenticator, UnavailableReason,
};
use crate::container::decode_request;
use crate::design_a::{verify_design_a, DesignAResult};
use crate::evidence::RootedLineageEvidence;
use crate::finality::{FinalityResult, HistoryMode};
use crate::frozen::{FrozenStepVerifier, VerifierIdentity};
use crate::verdict::{FailureCode, LineageStatus, VerificationFailure};

/// What the caller asks the service to establish.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum VerificationMode {
    /// Design A only: `LINEAGE_PROOF_VALID` at best.
    DesignAOnly,
    /// Full rooted lineage: `VERIFIED` at best.
    RootedLineage,
}

impl VerificationMode {
    /// Parses a mode name (`"design-a"` or `"rooted"`). Anything else is
    /// `UNSUPPORTED_MODE`; there is no fallback.
    ///
    /// # Errors
    ///
    /// `UNSUPPORTED_MODE`.
    pub fn parse(name: &str) -> Result<Self, VerificationFailure> {
        match name {
            "design-a" => Ok(Self::DesignAOnly),
            "rooted" => Ok(Self::RootedLineage),
            _ => Err(VerificationFailure::new(FailureCode::UnsupportedMode)),
        }
    }

    /// Stable wire name.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::DesignAOnly => "design-a",
            Self::RootedLineage => "rooted",
        }
    }
}

/// State of one verification obligation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ObligationStatus {
    /// Ran and passed.
    Passed,
    /// Ran and failed (the result's failure says how).
    Failed,
    /// Its dependency is not wired in this build.
    Pending,
    /// Not run: not requested, or blocked by an earlier failure or pending
    /// dependency.
    NotEvaluated,
}

impl ObligationStatus {
    /// Stable wire name.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Passed => "PASSED",
            Self::Failed => "FAILED",
            Self::Pending => "PENDING",
            Self::NotEvaluated => "NOT_EVALUATED",
        }
    }
}

impl fmt::Display for ObligationStatus {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Every obligation of the acceptance order, and where each one stands.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct Obligations {
    /// Canonical parse of the evidence.
    pub parse: ObligationStatus,
    /// `chain_ref` against the verifier's own view (CR-1 … CR-6).
    pub chain_context: ObligationStatus,
    /// Root authentication (R1 … R8).
    pub root: ObligationStatus,
    /// Design A (§B).
    pub design_a: ObligationStatus,
    /// Terminal authentication (T1 … T7).
    pub terminal: ObligationStatus,
    /// Height context `h_root < h_term ≤ r.height` (A5).
    pub context: ObligationStatus,
    /// Finality, freshness and checkpoint policy (CR-7 … CR-9).
    pub finality: ObligationStatus,
}

impl Obligations {
    const fn none() -> Self {
        Self {
            parse: ObligationStatus::NotEvaluated,
            chain_context: ObligationStatus::NotEvaluated,
            root: ObligationStatus::NotEvaluated,
            design_a: ObligationStatus::NotEvaluated,
            terminal: ObligationStatus::NotEvaluated,
            context: ObligationStatus::NotEvaluated,
            finality: ObligationStatus::NotEvaluated,
        }
    }

    const fn all(self) -> [ObligationStatus; 7] {
        [
            self.parse,
            self.chain_context,
            self.root,
            self.design_a,
            self.terminal,
            self.context,
            self.finality,
        ]
    }
}

/// Verifiable facts about a run, for logs, receipts and the demo.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VerificationEvidence {
    /// The verifier that ran.
    pub verifier: VerifierIdentity,
    /// `N`, once parsed.
    pub steps: Option<usize>,
    /// `chain_ref.id`, once parsed.
    pub chain_ref_id: Option<[u8; 32]>,
    /// Statement-keyed id (§G3), once parsed with a `chain_ref`.
    pub lineage_statement_id: Option<[u8; 32]>,
    /// History mode, once the chain context is authenticated.
    pub history_mode: Option<HistoryMode>,
    /// Root height, once authenticated.
    pub root_height: Option<u32>,
    /// Terminal height, once authenticated.
    pub terminal_height: Option<u32>,
    /// Per-step proof verification times, if Design A ran to completion.
    pub step_verify_times: Vec<Duration>,
    /// Design-A wall time, if it ran to completion.
    pub design_a_time: Option<Duration>,
    /// Wall time of the whole request.
    pub total_time: Duration,
}

impl VerificationEvidence {
    fn new(verifier: VerifierIdentity) -> Self {
        Self {
            verifier,
            steps: None,
            chain_ref_id: None,
            lineage_statement_id: None,
            history_mode: None,
            root_height: None,
            terminal_height: None,
            step_verify_times: Vec::new(),
            design_a_time: None,
            total_time: Duration::ZERO,
        }
    }

    fn record_evidence(&mut self, evidence: &RootedLineageEvidence) {
        self.steps = Some(evidence.len());
        self.chain_ref_id = evidence.chain_ref().map(|r| r.id());
        self.lineage_statement_id = evidence.lineage_statement_id();
    }
}

/// The one result of a lineage request.
#[derive(Debug, Clone, PartialEq, Eq)]
#[must_use = "a lineage result must be checked"]
pub struct LineageResult {
    /// What was asked.
    pub mode: VerificationMode,
    /// The single status (§H).
    pub status: LineageStatus,
    /// The first failure or, for `PENDING_AUTHENTICATION`, the first pending
    /// obligation.
    pub failure: Option<VerificationFailure>,
    /// Where each obligation stands.
    pub obligations: Obligations,
    /// Facts about the run.
    pub evidence: VerificationEvidence,
}

impl LineageResult {
    /// The only predicate a matcher may use to move a trade to `Verified`.
    #[must_use]
    pub const fn permits_trade_verification(&self) -> bool {
        self.status.is_rooted_lineage_verified()
    }

    /// API-facing JSON. Public data only.
    #[must_use]
    pub fn to_json(&self) -> Value {
        let hex32 = |v: Option<[u8; 32]>| v.map(hex::encode);
        let micros = |d: Duration| u64::try_from(d.as_micros()).unwrap_or(u64::MAX);
        let o = self.obligations;
        let v = &self.evidence.verifier;
        json!({
            "mode": self.mode.as_str(),
            "status": self.status.as_str(),
            "code": self.failure.map(|f| f.code().as_str()),
            "research_code": self.failure.map(|f| f.research_code()),
            "step": self.failure.and_then(|f| f.step()),
            "message": self.failure.map(|f| f.message()),
            "obligations": {
                "parse": o.parse.as_str(),
                "chain_context": o.chain_context.as_str(),
                "root": o.root.as_str(),
                "design_a": o.design_a.as_str(),
                "terminal": o.terminal.as_str(),
                "context": o.context.as_str(),
                "finality": o.finality.as_str(),
            },
            "verifier": {
                "verifier_id": v.verifier_id,
                "relation": v.relation,
                "proof_system": v.proof_system,
                "vk_id": hex::encode(v.vk_id),
                "params_k": v.params_k,
                "params_sha256": hex::encode(v.params_sha256),
                "step_proof_len": v.step_proof_len,
            },
            "steps": self.evidence.steps,
            "chain_ref_id": hex32(self.evidence.chain_ref_id),
            "lineage_statement_id": hex32(self.evidence.lineage_statement_id),
            "history_mode": self.evidence.history_mode.map(|m| m.to_string()),
            "root_height": self.evidence.root_height,
            "terminal_height": self.evidence.terminal_height,
            "timing_us": {
                "steps": self.evidence.step_verify_times.iter().map(|d| micros(*d)).collect::<Vec<_>>(),
                "design_a": self.evidence.design_a_time.map(micros),
                "total": micros(self.evidence.total_time),
            },
        })
    }
}

/// The production lineage verification service.
#[derive(Debug)]
pub struct LineageVerificationService<C, R, T> {
    verifier: FrozenStepVerifier,
    chain: C,
    root: R,
    terminal: T,
}

/// The Phase 1 service: frozen Design A, and no authentication layer wired.
pub type Phase1LineageService = LineageVerificationService<
    NotWiredChainContext,
    NotWiredRootAuthenticator,
    NotWiredTerminalAuthenticator,
>;

impl Phase1LineageService {
    /// The Phase 1 configuration. No path through it can return `VERIFIED`.
    #[must_use]
    pub const fn phase1(verifier: FrozenStepVerifier) -> Self {
        Self {
            verifier,
            chain: NotWiredChainContext,
            root: NotWiredRootAuthenticator,
            terminal: NotWiredTerminalAuthenticator,
        }
    }
}

/// Maps an unavailable dependency: `NotWired` leaves the obligation pending
/// (`Ok`); any run-time unavailability is a stop with `INTERNAL_ERROR` (`Err`).
fn unavailable(
    reason: UnavailableReason,
    pending: FailureCode,
) -> Result<VerificationFailure, VerificationFailure> {
    match reason {
        UnavailableReason::NotWired => Ok(VerificationFailure::new(pending)),
        UnavailableReason::ViewUnavailable
        | UnavailableReason::Timeout
        | UnavailableReason::NodeAborted => Err(VerificationFailure::new(
            FailureCode::AuthenticationUnavailable,
        )),
    }
}

fn invalidated(finality_violation: bool) -> VerificationFailure {
    VerificationFailure::new(if finality_violation {
        FailureCode::FinalityViolation
    } else {
        FailureCode::ReferenceInvalidated
    })
}

fn guarded<T>(f: impl FnOnce() -> T) -> Result<T, VerificationFailure> {
    catch_unwind(AssertUnwindSafe(f))
        .map_err(|_| VerificationFailure::new(FailureCode::InternalVerifierError))
}

impl<C, R, T> LineageVerificationService<C, R, T>
where
    C: ChainContextAuthenticator,
    R: RootAuthenticator,
    T: TerminalAuthenticator,
{
    /// A service over the given adapters. Adapters outside this crate can only
    /// report failures or unavailability: authentication successes are sealed
    /// (see [`crate::auth`]).
    #[must_use]
    pub const fn new(verifier: FrozenStepVerifier, chain: C, root: R, terminal: T) -> Self {
        Self {
            verifier,
            chain,
            root,
            terminal,
        }
    }

    /// The frozen verifier's identity.
    #[must_use]
    pub const fn identity(&self) -> &VerifierIdentity {
        self.verifier.identity()
    }

    /// Design A over canonical evidence.
    pub fn verify_design_a(&self, evidence: &RootedLineageEvidence) -> DesignAResult {
        guarded(|| verify_design_a(&self.verifier, evidence))
            .unwrap_or_else(DesignAResult::InternalError)
    }

    /// Chain-context authentication of `chain_ref`.
    pub fn verify_chain_context(&self, evidence: &RootedLineageEvidence) -> ChainIdentityResult {
        let Some(chain_ref) = evidence.chain_ref() else {
            return ChainIdentityResult::Rejected(VerificationFailure::new(
                FailureCode::ChainContextMissing,
            ));
        };
        guarded(|| self.chain.authenticate_chain_ref(chain_ref))
            .unwrap_or_else(ChainIdentityResult::InternalError)
    }

    /// Root authentication. Requires an authenticated chain context.
    pub fn verify_root(
        &self,
        evidence: &RootedLineageEvidence,
        context: &AuthenticatedChainContext,
    ) -> RootAuthenticationResult {
        guarded(|| self.root.authenticate_root(evidence.root_cmx(), context))
            .unwrap_or_else(RootAuthenticationResult::InternalError)
    }

    /// Terminal authentication. Requires an authenticated chain context.
    pub fn verify_terminal(
        &self,
        evidence: &RootedLineageEvidence,
        context: &AuthenticatedChainContext,
    ) -> TerminalAuthenticationResult {
        guarded(|| {
            self.terminal
                .authenticate_terminal(evidence.terminal_cmx(), context)
        })
        .unwrap_or_else(TerminalAuthenticationResult::InternalError)
    }

    /// Decodes a version-2 request and verifies it in `mode`.
    pub fn verify_request(&self, mode: VerificationMode, bytes: &[u8]) -> LineageResult {
        let started = Instant::now();
        match decode_request(bytes) {
            Ok(request) => self.verify(mode, &request.evidence),
            Err(failure) => {
                let mut evidence = VerificationEvidence::new(*self.identity());
                evidence.total_time = started.elapsed();
                let mut obligations = Obligations::none();
                obligations.parse = ObligationStatus::Failed;
                LineageResult {
                    mode,
                    status: failure.status(),
                    failure: Some(failure),
                    obligations,
                    evidence,
                }
            }
        }
    }

    /// Verifies canonical evidence in `mode`.
    pub fn verify(
        &self,
        mode: VerificationMode,
        evidence: &RootedLineageEvidence,
    ) -> LineageResult {
        match mode {
            VerificationMode::DesignAOnly => self.verify_design_a_only(evidence),
            VerificationMode::RootedLineage => self.verify_rooted_lineage(evidence),
        }
    }

    fn verify_design_a_only(&self, evidence: &RootedLineageEvidence) -> LineageResult {
        let started = Instant::now();
        let mut ev = VerificationEvidence::new(*self.identity());
        ev.record_evidence(evidence);
        let mut obligations = Obligations::none();
        obligations.parse = ObligationStatus::Passed;
        let da = self.verify_design_a(evidence);
        let failure = da.failure().copied();
        obligations.design_a = if failure.is_none() {
            ObligationStatus::Passed
        } else {
            ObligationStatus::Failed
        };
        if let DesignAResult::LineageProofValid(report) = &da {
            ev.step_verify_times.clone_from(&report.step_verify_times);
            ev.design_a_time = Some(report.total_time);
        }
        ev.total_time = started.elapsed();
        LineageResult {
            mode: VerificationMode::DesignAOnly,
            status: da.status(),
            failure,
            obligations,
            evidence: ev,
        }
    }

    /// Full rooted-lineage orchestration (see the module documentation).
    pub fn verify_rooted_lineage(&self, evidence: &RootedLineageEvidence) -> LineageResult {
        let started = Instant::now();
        let mut ev = VerificationEvidence::new(*self.identity());
        ev.record_evidence(evidence);
        let mut ob = Obligations::none();
        ob.parse = ObligationStatus::Passed;
        let outcome = self.rooted_steps(evidence, &mut ob, &mut ev);
        ev.total_time = started.elapsed();
        let (status, failure) = match outcome {
            Err(failure) => (failure.status(), Some(failure)),
            Ok(first_pending) => finish(ob, first_pending),
        };
        LineageResult {
            mode: VerificationMode::RootedLineage,
            status,
            failure,
            obligations: ob,
            evidence: ev,
        }
    }

    /// Runs the obligations. `Err` is a definitive stop with its failure;
    /// `Ok` carries the first pending obligation, if any.
    fn rooted_steps(
        &self,
        evidence: &RootedLineageEvidence,
        ob: &mut Obligations,
        ev: &mut VerificationEvidence,
    ) -> Result<Option<VerificationFailure>, VerificationFailure> {
        let mut first_pending: Option<VerificationFailure> = None;
        // Marks `slot` pending (Ok) or failed (Err) for an unavailable dependency.
        let mut on_unavailable = |slot: &mut ObligationStatus, reason, pending_code| {
            let outcome = unavailable(reason, pending_code);
            match outcome {
                Ok(f) => {
                    *slot = ObligationStatus::Pending;
                    first_pending.get_or_insert(f);
                    Ok(())
                }
                Err(f) => {
                    *slot = ObligationStatus::Failed;
                    Err(f)
                }
            }
        };
        let fail = |slot: &mut ObligationStatus, f: VerificationFailure| {
            *slot = ObligationStatus::Failed;
            f
        };

        // 1. chain_ref identity (CR-1 … CR-6).
        let context = match self.verify_chain_context(evidence) {
            ChainIdentityResult::Authenticated(c) => {
                ob.chain_context = ObligationStatus::Passed;
                ev.history_mode = Some(c.history_mode());
                Some(c)
            }
            ChainIdentityResult::Invalidated { finality_violation } => {
                return Err(fail(&mut ob.chain_context, invalidated(finality_violation)))
            }
            ChainIdentityResult::Rejected(f) | ChainIdentityResult::InternalError(f) => {
                return Err(fail(&mut ob.chain_context, f))
            }
            ChainIdentityResult::Unavailable(reason) => {
                on_unavailable(
                    &mut ob.chain_context,
                    reason,
                    FailureCode::ChainContextNotAuthenticated,
                )?;
                None
            }
        };

        // 2. Root (R1 … R8), against the authenticated context only.
        let root = match &context {
            None => None,
            Some(c) => match self.verify_root(evidence, c) {
                RootAuthenticationResult::Authenticated(r) => {
                    ob.root = ObligationStatus::Passed;
                    ev.root_height = Some(r.height());
                    Some(r)
                }
                RootAuthenticationResult::Rejected(f)
                | RootAuthenticationResult::InternalError(f) => return Err(fail(&mut ob.root, f)),
                RootAuthenticationResult::Unavailable(reason) => {
                    on_unavailable(&mut ob.root, reason, FailureCode::RootNotAuthenticated)?;
                    None
                }
            },
        };

        // 3. Design A: needs nothing from Layer B.
        match self.verify_design_a(evidence) {
            DesignAResult::LineageProofValid(report) => {
                ob.design_a = ObligationStatus::Passed;
                ev.step_verify_times = report.step_verify_times;
                ev.design_a_time = Some(report.total_time);
            }
            DesignAResult::Rejected(f) | DesignAResult::InternalError(f) => {
                return Err(fail(&mut ob.design_a, f))
            }
        }

        // 4. Terminal (T1 … T7), against the authenticated context only.
        let terminal = match &context {
            None => None,
            Some(c) => match self.verify_terminal(evidence, c) {
                TerminalAuthenticationResult::Authenticated(t) => {
                    ob.terminal = ObligationStatus::Passed;
                    ev.terminal_height = Some(t.height());
                    Some(t)
                }
                TerminalAuthenticationResult::Rejected(f)
                | TerminalAuthenticationResult::InternalError(f) => {
                    return Err(fail(&mut ob.terminal, f))
                }
                TerminalAuthenticationResult::Unavailable(reason) => {
                    on_unavailable(
                        &mut ob.terminal,
                        reason,
                        FailureCode::TerminalNotAuthenticated,
                    )?;
                    None
                }
            },
        };

        // 5. Context A5: h_root < h_term ≤ r.height.
        if let (Some(c), Some(r), Some(t)) = (&context, &root, &terminal) {
            if r.height() < t.height() && t.height() <= c.chain_ref().height() {
                ob.context = ObligationStatus::Passed;
            } else {
                return Err(fail(
                    &mut ob.context,
                    VerificationFailure::new(FailureCode::ContextMismatch),
                ));
            }
        }

        // 6. Finality and policy (CR-7 … CR-9), against the authenticated context only.
        if let Some(c) = &context {
            let result = guarded(|| self.chain.evaluate_finality(c))
                .unwrap_or_else(FinalityResult::InternalError);
            match result {
                FinalityResult::Finalized(fin) => {
                    ob.finality = ObligationStatus::Passed;
                    ev.history_mode = Some(fin.history_mode());
                }
                // Not a stop: PENDING_FINALITY is reported only if every other
                // obligation passed (see `finish`).
                FinalityResult::NotFinal => ob.finality = ObligationStatus::Failed,
                FinalityResult::Invalidated { finality_violation } => {
                    return Err(fail(&mut ob.finality, invalidated(finality_violation)))
                }
                FinalityResult::CheckpointTrustRequired => {
                    return Err(fail(
                        &mut ob.finality,
                        VerificationFailure::new(FailureCode::CheckpointedHistory),
                    ))
                }
                FinalityResult::Rejected(f) | FinalityResult::InternalError(f) => {
                    return Err(fail(&mut ob.finality, f))
                }
                FinalityResult::Unavailable(reason) => on_unavailable(
                    &mut ob.finality,
                    reason,
                    FailureCode::ChainContextNotAuthenticated,
                )?,
            }
        }
        Ok(first_pending)
    }
}

/// Final status once no obligation stopped the run.
fn finish(
    ob: Obligations,
    first_pending: Option<VerificationFailure>,
) -> (LineageStatus, Option<VerificationFailure>) {
    if let Some(f) = first_pending {
        return (LineageStatus::PendingAuthentication, Some(f));
    }
    if ob.finality == ObligationStatus::Failed {
        return (
            LineageStatus::PendingFinality,
            Some(VerificationFailure::new(FailureCode::ReferenceNotFinal)),
        );
    }
    if ob.all().iter().all(|s| *s == ObligationStatus::Passed) {
        return (LineageStatus::RootedLineageVerified, None);
    }
    // Defensive: an obligation neither passed, failed nor pending. Never accept.
    (
        LineageStatus::InternalError,
        Some(VerificationFailure::new(FailureCode::InternalVerifierError)),
    )
}

#[cfg(test)]
mod tests {
    //! Orchestration logic with crate-internal test doubles.
    //!
    //! These doubles are the only code that constructs sealed authentication
    //! successes, and they exist only under `cfg(test)`. They check that
    //! `VERIFIED` needs every obligation, and that each obligation's failure or
    //! pendency blocks it. They are not a production path.

    use std::cell::Cell;

    use super::*;
    use crate::auth::{AuthenticatedRoot, AuthenticatedTerminal};
    use crate::chain_ref::ChainRef;
    use crate::evidence::{LineagePublicInputs, LineageStepProof};
    use crate::field::{CanonicalCmx, CanonicalNullifier, CanonicalOrchardRoot};
    use crate::finality::FinalizedReference;

    const S1: &[u8] = include_bytes!("../tests/fixtures/step-proofs/lb-s1.bin");
    const S2: &[u8] = include_bytes!("../tests/fixtures/step-proofs/lb-s2.bin");
    const S3: &[u8] = include_bytes!("../tests/fixtures/step-proofs/lb-s3.bin");

    fn cmx(h: &str) -> CanonicalCmx {
        CanonicalCmx::from_slice(&hex::decode(h).unwrap()).unwrap()
    }

    fn nf(h: &str) -> CanonicalNullifier {
        CanonicalNullifier::from_slice(&hex::decode(h).unwrap()).unwrap()
    }

    /// The frozen N = 3 fixture (values from `tests/fixtures/fixtures.json`).
    fn n3() -> RootedLineageEvidence {
        let x0 = "3480bccf0baa4eef0fcbfc4070d9559393deac4af74311fce82e8cba59086b11";
        let out1 = "af15792c2441c03a6685d3f69fcb9a8d2050ff16cd6f1cc3c122879b53291c2f";
        let out2 = "30690033b14a7a919371e5b0413741b15a31794e4909c1b26bc52f660cd91a1d";
        let out3 = "261f17308aa5a3f29674bfc992dfed28e0e202939f709063ce258bd47404d422";
        let step = |a, n, b, p: &[u8]| LineageStepProof {
            public_inputs: LineagePublicInputs {
                cmx_old: cmx(a),
                nf_old: nf(n),
                cmx_new: cmx(b),
            },
            proof: crate::evidence::CanonicalStepProof::from_slice(p).unwrap(),
        };
        let root = CanonicalOrchardRoot::from_slice(
            &hex::decode("e27b31d544c04a79d3e1b84170cdc8d12eca5c2807dbe63d16562d3a4d2dd838")
                .unwrap(),
        )
        .unwrap();
        let chain_ref = ChainRef::new([0x29; 32], 4, [0x88; 32], root).unwrap();
        RootedLineageEvidence::new(
            cmx(x0),
            cmx(out3),
            vec![
                step(
                    x0,
                    "263f54bb1d54147bae9f56328c99786d2a57ecb6c13e2bd780ae9e1e50ca042d",
                    out1,
                    S1,
                ),
                step(
                    out1,
                    "45a653797da4d7a759ca665cb839b5f9fc847ea8373f9b38d1e72142e40c782f",
                    out2,
                    S2,
                ),
                step(
                    out2,
                    "862dcdb2d4b1ab444a58b381de33df16e9ae1a1c26cfb2eb6b2a3131d0d04f18",
                    out3,
                    S3,
                ),
            ],
            Some(chain_ref),
        )
        .unwrap()
    }

    /// Test-only chain context: authenticates, then reports `finality`.
    struct Chain {
        tip: u32,
        finality: FinalityResult,
        calls: Cell<u32>,
    }

    impl ChainContextAuthenticator for Chain {
        fn authenticate_chain_ref(&self, r: &ChainRef) -> ChainIdentityResult {
            self.calls.set(self.calls.get() + 1);
            ChainIdentityResult::Authenticated(AuthenticatedChainContext::new(
                *r,
                self.tip,
                HistoryMode::FullValidation,
            ))
        }
        fn evaluate_finality(&self, _: &AuthenticatedChainContext) -> FinalityResult {
            self.finality
        }
    }

    struct Root(RootAuthenticationResult);
    impl RootAuthenticator for Root {
        fn authenticate_root(
            &self,
            _: &CanonicalCmx,
            _: &AuthenticatedChainContext,
        ) -> RootAuthenticationResult {
            self.0
        }
    }

    struct Term(TerminalAuthenticationResult);
    impl TerminalAuthenticator for Term {
        fn authenticate_terminal(
            &self,
            _: &CanonicalCmx,
            _: &AuthenticatedChainContext,
        ) -> TerminalAuthenticationResult {
            self.0
        }
    }

    fn finalized() -> FinalityResult {
        FinalityResult::Finalized(FinalizedReference::new(HistoryMode::FullValidation, 3))
    }

    fn root_ok(h: u32) -> RootAuthenticationResult {
        RootAuthenticationResult::Authenticated(AuthenticatedRoot::new(h, 3, [0x36; 32]))
    }

    fn term_ok(h: u32) -> TerminalAuthenticationResult {
        TerminalAuthenticationResult::Authenticated(AuthenticatedTerminal::new(h, 25))
    }

    fn run(
        finality: FinalityResult,
        root: RootAuthenticationResult,
        term: TerminalAuthenticationResult,
        ev: &RootedLineageEvidence,
    ) -> LineageResult {
        let chain = Chain {
            tip: 7,
            finality,
            calls: Cell::new(0),
        };
        let s = LineageVerificationService::new(
            FrozenStepVerifier::load().unwrap(),
            chain,
            Root(root),
            Term(term),
        );
        s.verify_rooted_lineage(ev)
    }

    #[test]
    fn orchestration_rules() {
        let ev = n3();
        // Every obligation passes: VERIFIED.
        let r = run(finalized(), root_ok(1), term_ok(4), &ev);
        assert_eq!(
            r.status,
            LineageStatus::RootedLineageVerified,
            "{:?}",
            r.failure
        );
        assert!(r.permits_trade_verification());
        assert!(r
            .obligations
            .all()
            .iter()
            .all(|o| *o == ObligationStatus::Passed));
        assert_eq!(r.evidence.root_height, Some(1));
        assert_eq!(r.evidence.terminal_height, Some(4));

        // Not final: PENDING_FINALITY, never VERIFIED.
        let r = run(FinalityResult::NotFinal, root_ok(1), term_ok(4), &ev);
        assert_eq!(r.status, LineageStatus::PendingFinality);
        assert_eq!(r.failure.unwrap().research_code(), "NOT_FINAL");

        // Each unwired dependency blocks VERIFIED.
        let nw = UnavailableReason::NotWired;
        let r = run(FinalityResult::Unavailable(nw), root_ok(1), term_ok(4), &ev);
        assert_eq!(r.status, LineageStatus::PendingAuthentication);
        let r = run(
            finalized(),
            RootAuthenticationResult::Unavailable(nw),
            term_ok(4),
            &ev,
        );
        assert_eq!(r.status, LineageStatus::PendingAuthentication);
        assert_eq!(r.failure.unwrap().code(), FailureCode::RootNotAuthenticated);
        assert_eq!(r.obligations.context, ObligationStatus::NotEvaluated);
        let r = run(
            finalized(),
            root_ok(1),
            TerminalAuthenticationResult::Unavailable(nw),
            &ev,
        );
        assert_eq!(r.status, LineageStatus::PendingAuthentication);
        assert_eq!(
            r.failure.unwrap().code(),
            FailureCode::TerminalNotAuthenticated
        );

        // Run-time unavailability is INTERNAL_ERROR.
        let r = run(
            finalized(),
            RootAuthenticationResult::Unavailable(UnavailableReason::Timeout),
            term_ok(4),
            &ev,
        );
        assert_eq!(r.status, LineageStatus::InternalError);

        // Context A5: h_root < h_term ≤ r.height.
        for (h_root, h_term) in [(4, 4), (5, 4), (1, 5)] {
            let r = run(finalized(), root_ok(h_root), term_ok(h_term), &ev);
            assert_eq!(r.status, LineageStatus::Rejected, "{h_root} {h_term}");
            assert_eq!(r.failure.unwrap().research_code(), "CONTEXT");
        }

        // Rejections from Layer B adapters keep their code and stop the run.
        let rej = RootAuthenticationResult::Rejected(VerificationFailure::layer_b(
            FailureCode::RootAuthenticationFailed,
            "ROOT_POLICY_ISSUER",
        ));
        let r = run(finalized(), rej, term_ok(4), &ev);
        assert_eq!(r.status, LineageStatus::Rejected);
        assert_eq!(r.failure.unwrap().research_code(), "ROOT_POLICY_ISSUER");
        assert_eq!(r.obligations.design_a, ObligationStatus::NotEvaluated);
        let r = run(
            finalized(),
            root_ok(1),
            TerminalAuthenticationResult::Rejected(VerificationFailure::new(
                FailureCode::TerminalUnsupported,
            )),
            &ev,
        );
        assert_eq!(r.status, LineageStatus::Unsupported);
        assert_eq!(r.failure.unwrap().research_code(), "TERM_NOT_ZSA_ACTION");

        // Finality outcomes.
        let r = run(
            FinalityResult::CheckpointTrustRequired,
            root_ok(1),
            term_ok(4),
            &ev,
        );
        assert_eq!(r.status, LineageStatus::CheckpointTrustRequired);
        let r = run(
            FinalityResult::Invalidated {
                finality_violation: true,
            },
            root_ok(1),
            term_ok(4),
            &ev,
        );
        assert_eq!(r.status, LineageStatus::InvalidatedByReorg);
        assert_eq!(r.failure.unwrap().research_code(), "FINALITY_VIOLATION");

        // A false proof is REJECTED even with every Layer-B obligation passing.
        let mut steps = ev.steps().to_vec();
        let proof0 = steps[0].proof.clone();
        steps[0].proof = steps[1].proof.clone();
        steps[1].proof = proof0;
        let bad = RootedLineageEvidence::new(
            *ev.root_cmx(),
            *ev.terminal_cmx(),
            steps,
            ev.chain_ref().copied(),
        )
        .unwrap();
        let r = run(finalized(), root_ok(1), term_ok(4), &bad);
        assert_eq!(r.status, LineageStatus::Rejected);
        assert_eq!(r.failure.unwrap().research_code(), "DA_PROOF(0)");
    }

    #[test]
    fn finish_never_accepts_an_incomplete_ledger() {
        let mut ob = Obligations::none();
        assert_eq!(finish(ob, None).0, LineageStatus::InternalError);
        ob.parse = ObligationStatus::Passed;
        ob.design_a = ObligationStatus::Passed;
        assert_eq!(finish(ob, None).0, LineageStatus::InternalError);
    }
}
