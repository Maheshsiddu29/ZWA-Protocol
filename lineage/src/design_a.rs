//! Design-A rooted-lineage structure verification (contract §B, M2).
//!
//! `AcceptDA` of the frozen research (`r3e/experiments/src/design_a.rs`,
//! `Mode::Single`), rewritten for production. Input is canonical evidence, so
//! `1 ≤ N ≤ 1024` and every tuple and proof is already canonical (§B6). Then,
//! in the research order (§B7):
//!
//! 1. every proof verifies, under VK\*, for its own tuple, in index order;
//! 2. `cmx_new[i] == cmx_old[i + 1]` for every `i < N − 1`;
//! 3. `cmx_old[0] == root_cmx`;
//! 4. `cmx_new[N − 1] == terminal_cmx`.
//!
//! The first failure is reported with its step index. There is no batch path:
//! acceptance never depends on a batch check (§B1). Steps are neither
//! reordered nor deduplicated; a duplicated or reordered step fails a join.
//!
//! A pass is `LINEAGE_PROOF_VALID`. It is not `VERIFIED`: nothing here
//! authenticates the root, the terminal or any chain context.

use std::time::{Duration, Instant};

use crate::evidence::RootedLineageEvidence;
use crate::frozen::{FrozenStepVerifier, StepCheck, VerifierIdentity};
use crate::verdict::{FailureCode, LineageStatus, VerificationFailure};

/// What a Design-A pass establishes, plus measurements.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DesignAReport {
    /// `N`.
    pub steps: usize,
    /// The verifier that ran.
    pub verifier: VerifierIdentity,
    /// Wall time of each step's proof verification, in step order.
    pub step_verify_times: Vec<Duration>,
    /// Wall time of the whole Design-A check.
    pub total_time: Duration,
}

/// Outcome of [`verify_design_a`].
#[derive(Debug, Clone, PartialEq, Eq)]
#[must_use = "a Design-A result must be checked"]
pub enum DesignAResult {
    /// `LINEAGE_PROOF_VALID`.
    LineageProofValid(DesignAReport),
    /// `REJECTED`, with the first failure.
    Rejected(VerificationFailure),
    /// `INTERNAL_ERROR`: no verdict about the evidence.
    InternalError(VerificationFailure),
}

impl DesignAResult {
    /// The status this result maps to.
    pub const fn status(&self) -> LineageStatus {
        match self {
            Self::LineageProofValid(_) => LineageStatus::LineageProofValid,
            Self::Rejected(_) => LineageStatus::Rejected,
            Self::InternalError(_) => LineageStatus::InternalError,
        }
    }

    /// The failure, if any.
    #[must_use]
    pub const fn failure(&self) -> Option<&VerificationFailure> {
        match self {
            Self::LineageProofValid(_) => None,
            Self::Rejected(f) | Self::InternalError(f) => Some(f),
        }
    }
}

/// Runs Design A over canonical evidence with the frozen verifier.
pub fn verify_design_a(
    verifier: &FrozenStepVerifier,
    evidence: &RootedLineageEvidence,
) -> DesignAResult {
    let started = Instant::now();
    let steps = evidence.steps();
    let mut step_verify_times = Vec::with_capacity(steps.len());
    for (i, step) in steps.iter().enumerate() {
        let t = Instant::now();
        let check = verifier.verify_step(step);
        step_verify_times.push(t.elapsed());
        match check {
            StepCheck::Valid => {}
            StepCheck::Invalid => {
                return DesignAResult::Rejected(VerificationFailure::at_step(
                    FailureCode::InvalidStepProof,
                    i,
                ))
            }
            StepCheck::Internal => {
                return DesignAResult::InternalError(VerificationFailure::at_step(
                    FailureCode::InternalVerifierError,
                    i,
                ))
            }
        }
    }
    // Canonical encodings make byte equality the same as field equality.
    for (i, pair) in steps.windows(2).enumerate() {
        if let [a, b] = pair {
            if a.public_inputs.cmx_new != b.public_inputs.cmx_old {
                return DesignAResult::Rejected(VerificationFailure::at_step(
                    FailureCode::BrokenContinuity,
                    i,
                ));
            }
        }
    }
    let (Some(first), Some(last)) = (steps.first(), steps.last()) else {
        // Unreachable for canonical evidence; fail closed regardless.
        return DesignAResult::Rejected(VerificationFailure::new(FailureCode::EmptyLineage));
    };
    if first.public_inputs.cmx_old != *evidence.root_cmx() {
        return DesignAResult::Rejected(VerificationFailure::new(FailureCode::RootMismatch));
    }
    if last.public_inputs.cmx_new != *evidence.terminal_cmx() {
        return DesignAResult::Rejected(VerificationFailure::new(FailureCode::TerminalMismatch));
    }
    DesignAResult::LineageProofValid(DesignAReport {
        steps: steps.len(),
        verifier: *verifier.identity(),
        step_verify_times,
        total_time: started.elapsed(),
    })
}
