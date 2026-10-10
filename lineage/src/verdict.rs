//! Production status and failure model (contract §H, §F).
//!
//! One request yields exactly one [`LineageStatus`]. A failure carries a
//! stable machine-readable [`FailureCode`], the research-compatible code the
//! contract requires (`DA_PROOF(1)`, `ROOT_MALFORMED`, …), and the step index
//! where one applies. No message contains witness data: every value the
//! verifier ever sees is public.

use core::fmt;

/// The single outcome of one lineage verification.
///
/// Only [`LineageStatus::RootedLineageVerified`] may contribute to a trade's
/// `Verified` lifecycle state (contract §H). [`LineageStatus::LineageProofValid`]
/// is the Design-A-only result: every step proof verified under the frozen key
/// and the public tuples join from `root_cmx` to `terminal_cmx`. It says
/// nothing about whether the root is an authorized issuance, whether the
/// terminal is on chain, or about any chain context.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[must_use = "a lineage status must be checked"]
pub enum LineageStatus {
    /// `VERIFIED`: every obligation of the contract's acceptance order passed,
    /// including root, terminal and chain authentication and finality.
    RootedLineageVerified,
    /// `LINEAGE_PROOF_VALID`: Design A only (no root, terminal or chain
    /// authentication was requested or performed).
    LineageProofValid,
    /// `REJECTED`: the evidence is false or malformed for a supported request.
    Rejected,
    /// `PENDING_AUTHENTICATION`: every implemented check passed, but at least
    /// one required authentication layer is not wired in this build. Never a
    /// success. Phase 1 only; see `docs/lineage-integration.md`.
    PendingAuthentication,
    /// `PENDING_FINALITY`: everything passed except finality of `chain_ref`.
    PendingFinality,
    /// `INVALIDATED_BY_REORG`: the referenced block left the selected chain.
    InvalidatedByReorg,
    /// `CHECKPOINT_TRUST_REQUIRED`: checkpoint-committed history and CP mode off.
    CheckpointTrustRequired,
    /// `UNSUPPORTED`: outside the supported semantics, versions or modes.
    Unsupported,
    /// `INTERNAL_ERROR`: the verifier could not decide. Never a verdict.
    InternalError,
}

impl LineageStatus {
    /// Reports whether this is the full rooted-lineage success.
    ///
    /// This is the only predicate a matcher may use to move a trade to
    /// `Verified`.
    #[must_use]
    pub const fn is_rooted_lineage_verified(self) -> bool {
        matches!(self, Self::RootedLineageVerified)
    }

    /// Stable wire name.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::RootedLineageVerified => "VERIFIED",
            Self::LineageProofValid => "LINEAGE_PROOF_VALID",
            Self::Rejected => "REJECTED",
            Self::PendingAuthentication => "PENDING_AUTHENTICATION",
            Self::PendingFinality => "PENDING_FINALITY",
            Self::InvalidatedByReorg => "INVALIDATED_BY_REORG",
            Self::CheckpointTrustRequired => "CHECKPOINT_TRUST_REQUIRED",
            Self::Unsupported => "UNSUPPORTED",
            Self::InternalError => "INTERNAL_ERROR",
        }
    }
}

impl fmt::Display for LineageStatus {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Which public field a canonicalization failure refers to.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum FieldRole {
    /// `root_cmx`.
    RootCmx,
    /// `terminal_cmx`.
    TerminalCmx,
    /// `cmx_old` of a step.
    CmxOld,
    /// `nf_old` of a step.
    NfOld,
    /// `cmx_new` of a step.
    CmxNew,
}

/// Stable machine-readable failure codes.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum FailureCode {
    /// Request framing is malformed: tag, lengths, counts or trailing bytes.
    InvalidEncoding,
    /// The request container version is not supported.
    UnsupportedVersion,
    /// A public field is not a canonical `F_{q_P}` encoding.
    NonCanonicalField,
    /// `N = 0`.
    EmptyLineage,
    /// `N > 1024`.
    TooManySteps,
    /// The request names a verifying key other than the frozen VK\*.
    ForeignVerifier,
    /// A step proof is empty, has the wrong length or carries trailing bytes.
    NonCanonicalProof,
    /// A step proof does not verify for its own public tuple under VK\*.
    InvalidStepProof,
    /// `cmx_new[i] != cmx_old[i + 1]`.
    BrokenContinuity,
    /// `cmx_old[0] != root_cmx`.
    RootMismatch,
    /// `cmx_new[N - 1] != terminal_cmx`.
    TerminalMismatch,
    /// A rooted request carries no `chain_ref`.
    ChainContextMissing,
    /// `chain_ref` bytes are malformed (length, tag, height 0, root).
    ChainRefMalformed,
    /// `chain_ref` version is not supported.
    ChainRefUnsupportedVersion,
    /// The verifier's chain view rejected `chain_ref` (Layer B CR-3…CR-6).
    ChainContextRejected,
    /// Root authentication rejected `root_cmx` (Layer B R1–R8).
    RootAuthenticationFailed,
    /// Terminal authentication rejected `terminal_cmx` (Layer B T1–T7).
    TerminalAuthenticationFailed,
    /// The terminal is outside the supported semantics (vanilla terminal).
    TerminalUnsupported,
    /// `chain_ref` is on the selected chain but not final under policy.
    ReferenceNotFinal,
    /// `chain_ref` was seen `Accepted` and then reorganized away.
    ReferenceInvalidated,
    /// `chain_ref` was seen `Finalized` and then reorganized away (alarm).
    FinalityViolation,
    /// The view has checkpoint-committed history and CP mode is off.
    CheckpointedHistory,
    /// Root, terminal and `chain_ref` heights are inconsistent (A5).
    ContextMismatch,
    /// The chain context has not been authenticated in this build.
    ChainContextNotAuthenticated,
    /// `root_cmx` has not been authenticated in this build.
    RootNotAuthenticated,
    /// `terminal_cmx` has not been authenticated in this build.
    TerminalNotAuthenticated,
    /// The requested verification mode is not supported.
    UnsupportedMode,
    /// A wired authentication dependency failed at run time (view, RPC,
    /// timeout, aborted node).
    AuthenticationUnavailable,
    /// The verifier's own frozen key or parameters failed their identity check.
    VerifierIdentityMismatch,
    /// Any other failure inside the verifier (including a caught panic).
    InternalVerifierError,
}

impl FailureCode {
    /// Stable wire name.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::InvalidEncoding => "INVALID_ENCODING",
            Self::UnsupportedVersion => "UNSUPPORTED_VERSION",
            Self::NonCanonicalField => "NON_CANONICAL_FIELD",
            Self::EmptyLineage => "EMPTY_LINEAGE",
            Self::TooManySteps => "TOO_MANY_STEPS",
            Self::ForeignVerifier => "FOREIGN_VERIFIER",
            Self::NonCanonicalProof => "NON_CANONICAL_PROOF",
            Self::InvalidStepProof => "INVALID_STEP_PROOF",
            Self::BrokenContinuity => "BROKEN_CONTINUITY",
            Self::RootMismatch => "ROOT_MISMATCH",
            Self::TerminalMismatch => "TERMINAL_MISMATCH",
            Self::ChainContextMissing => "CHAIN_CONTEXT_MISSING",
            Self::ChainRefMalformed => "CHAIN_REF_MALFORMED",
            Self::ChainRefUnsupportedVersion => "CHAIN_REF_UNSUPPORTED_VERSION",
            Self::ChainContextRejected => "CHAIN_CONTEXT_REJECTED",
            Self::RootAuthenticationFailed => "ROOT_AUTHENTICATION_FAILED",
            Self::TerminalAuthenticationFailed => "TERMINAL_AUTHENTICATION_FAILED",
            Self::TerminalUnsupported => "TERMINAL_UNSUPPORTED",
            Self::ReferenceNotFinal => "REFERENCE_NOT_FINAL",
            Self::ReferenceInvalidated => "REFERENCE_INVALIDATED",
            Self::FinalityViolation => "FINALITY_VIOLATION",
            Self::CheckpointedHistory => "CHECKPOINTED_HISTORY",
            Self::ContextMismatch => "CONTEXT_MISMATCH",
            Self::ChainContextNotAuthenticated => "CHAIN_CONTEXT_NOT_AUTHENTICATED",
            Self::RootNotAuthenticated => "ROOT_NOT_AUTHENTICATED",
            Self::TerminalNotAuthenticated => "TERMINAL_NOT_AUTHENTICATED",
            Self::UnsupportedMode => "UNSUPPORTED_MODE",
            Self::AuthenticationUnavailable => "AUTHENTICATION_UNAVAILABLE",
            Self::VerifierIdentityMismatch => "VERIFIER_IDENTITY_MISMATCH",
            Self::InternalVerifierError => "INTERNAL_VERIFIER_ERROR",
        }
    }

    /// The status a failure with this code produces (contract §H).
    pub const fn status(self) -> LineageStatus {
        match self {
            Self::UnsupportedVersion
            | Self::ChainRefUnsupportedVersion
            | Self::TerminalUnsupported
            | Self::UnsupportedMode => LineageStatus::Unsupported,
            Self::ReferenceNotFinal => LineageStatus::PendingFinality,
            Self::ReferenceInvalidated | Self::FinalityViolation => {
                LineageStatus::InvalidatedByReorg
            }
            Self::CheckpointedHistory => LineageStatus::CheckpointTrustRequired,
            Self::ChainContextNotAuthenticated
            | Self::RootNotAuthenticated
            | Self::TerminalNotAuthenticated => LineageStatus::PendingAuthentication,
            Self::AuthenticationUnavailable
            | Self::VerifierIdentityMismatch
            | Self::InternalVerifierError => LineageStatus::InternalError,
            Self::InvalidEncoding
            | Self::NonCanonicalField
            | Self::EmptyLineage
            | Self::TooManySteps
            | Self::ForeignVerifier
            | Self::NonCanonicalProof
            | Self::InvalidStepProof
            | Self::BrokenContinuity
            | Self::RootMismatch
            | Self::TerminalMismatch
            | Self::ChainContextMissing
            | Self::ChainRefMalformed
            | Self::ChainContextRejected
            | Self::RootAuthenticationFailed
            | Self::TerminalAuthenticationFailed
            | Self::ContextMismatch => LineageStatus::Rejected,
        }
    }
}

impl fmt::Display for FailureCode {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// One failure: code, optional step index and field, and the Layer-B code an
/// authentication adapter reported.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct VerificationFailure {
    code: FailureCode,
    step: Option<usize>,
    field: Option<FieldRole>,
    layer_b_code: Option<&'static str>,
}

impl VerificationFailure {
    /// A failure with no step, field or Layer-B detail.
    pub const fn new(code: FailureCode) -> Self {
        Self {
            code,
            step: None,
            field: None,
            layer_b_code: None,
        }
    }

    /// A failure at step `index`.
    pub const fn at_step(code: FailureCode, index: usize) -> Self {
        Self {
            code,
            step: Some(index),
            field: None,
            layer_b_code: None,
        }
    }

    /// A canonicalization failure of `field` (at step `step`, for step fields).
    pub const fn non_canonical(field: FieldRole, step: Option<usize>) -> Self {
        Self {
            code: FailureCode::NonCanonicalField,
            step,
            field: Some(field),
            layer_b_code: None,
        }
    }

    /// A failure an authentication adapter reported with its Layer-B code
    /// (`ROOT_POLICY_ISSUER`, `TERM_ANCHOR`, `UNKNOWN`, …).
    pub const fn layer_b(code: FailureCode, layer_b_code: &'static str) -> Self {
        Self {
            code,
            step: None,
            field: None,
            layer_b_code: Some(layer_b_code),
        }
    }

    /// The machine-readable code.
    #[must_use]
    pub const fn code(&self) -> FailureCode {
        self.code
    }

    /// The step index, where one applies.
    #[must_use]
    pub const fn step(&self) -> Option<usize> {
        self.step
    }

    /// The field, for canonicalization failures.
    #[must_use]
    pub const fn field(&self) -> Option<FieldRole> {
        self.field
    }

    /// The status this failure produces.
    pub const fn status(&self) -> LineageStatus {
        self.code.status()
    }

    /// The research-compatible code (`ENGINEERING_LINEAGE_CONTRACT.md` §H).
    ///
    /// Codes the research has no name for (Phase 1 pending states, the
    /// verification mode) use the production name.
    #[must_use]
    pub fn research_code(&self) -> String {
        if let Some(code) = self.layer_b_code {
            return code.to_string();
        }
        let step = self.step.unwrap_or(0);
        match (self.code, self.field) {
            (FailureCode::NonCanonicalField, Some(FieldRole::RootCmx)) => "ROOT_MALFORMED".into(),
            (FailureCode::NonCanonicalField, Some(FieldRole::TerminalCmx)) => {
                "TERM_MALFORMED".into()
            }
            (FailureCode::NonCanonicalField, _) => format!("DA_NONCANONICAL({step})"),
            (FailureCode::InvalidEncoding | FailureCode::TooManySteps, _) => "MALFORMED".into(),
            (FailureCode::ChainRefMalformed, _) => "MALFORMED".into(),
            (FailureCode::UnsupportedVersion | FailureCode::ChainRefUnsupportedVersion, _) => {
                "UNSUPPORTED_VERSION".into()
            }
            (FailureCode::EmptyLineage, _) => "DA_EMPTY".into(),
            (FailureCode::ForeignVerifier, _) => "DA_FOREIGN_VK".into(),
            (FailureCode::NonCanonicalProof, _) => format!("DA_NONCANONICAL_PROOF({step})"),
            (FailureCode::InvalidStepProof, _) => format!("DA_PROOF({step})"),
            (FailureCode::BrokenContinuity, _) => format!("DA_JOIN({step})"),
            (FailureCode::RootMismatch, _) => "DA_ROOT".into(),
            (FailureCode::TerminalMismatch, _) => "DA_TERMINAL".into(),
            (FailureCode::ContextMismatch, _) => "CONTEXT".into(),
            (FailureCode::TerminalUnsupported, _) => "TERM_NOT_ZSA_ACTION".into(),
            (FailureCode::ReferenceNotFinal, _) => "NOT_FINAL".into(),
            (FailureCode::ReferenceInvalidated, _) => "INVALIDATED".into(),
            (FailureCode::AuthenticationUnavailable, _) => "VIEW_UNAVAILABLE".into(),
            (FailureCode::VerifierIdentityMismatch, _) => "DA_VK_IDENTITY".into(),
            (code, _) => code.as_str().into(),
        }
    }

    /// One line for operators and the demo. Contains only public data.
    #[must_use]
    pub fn message(&self) -> String {
        let at = self
            .step
            .map(|i| format!(" at step {i}"))
            .unwrap_or_default();
        let text = match self.code {
            FailureCode::InvalidEncoding => "the lineage request is not a well-formed container",
            FailureCode::UnsupportedVersion => "the lineage request version is not supported",
            FailureCode::NonCanonicalField => {
                "a public value is not a canonical Pallas field encoding"
            }
            FailureCode::EmptyLineage => "the lineage has no steps",
            FailureCode::TooManySteps => "the lineage has more than 1024 steps",
            FailureCode::ForeignVerifier => "the request names a verifying key other than VK*",
            FailureCode::NonCanonicalProof => "a step proof is not exactly 5,120 bytes",
            FailureCode::InvalidStepProof => "a step proof does not verify for its public tuple",
            FailureCode::BrokenContinuity => {
                "a step's cmx_new is not the next step's cmx_old"
            }
            FailureCode::RootMismatch => "the first step does not start at root_cmx",
            FailureCode::TerminalMismatch => "the last step does not end at terminal_cmx",
            FailureCode::ChainContextMissing => "the rooted request carries no chain_ref",
            FailureCode::ChainRefMalformed => "chain_ref is malformed",
            FailureCode::ChainRefUnsupportedVersion => "chain_ref version is not supported",
            FailureCode::ChainContextRejected => "the verifier's chain view rejects chain_ref",
            FailureCode::RootAuthenticationFailed => "root_cmx is not an authorized issuance",
            FailureCode::TerminalAuthenticationFailed => {
                "terminal_cmx is not an authenticated ZSA Action output"
            }
            FailureCode::TerminalUnsupported => "the terminal is outside the supported semantics",
            FailureCode::ReferenceNotFinal => "chain_ref is not yet final; re-submit later",
            FailureCode::ReferenceInvalidated => "chain_ref was reorganized out of the selected chain",
            FailureCode::FinalityViolation => {
                "a finalized chain_ref was reorganized out of the selected chain (alarm)"
            }
            FailureCode::CheckpointedHistory => {
                "the verifier's history is checkpoint-committed and checkpoint trust is not enabled"
            }
            FailureCode::ContextMismatch => "root, terminal and chain_ref heights are inconsistent",
            FailureCode::ChainContextNotAuthenticated => {
                "chain context authentication is not wired in this build"
            }
            FailureCode::RootNotAuthenticated => "root authentication is not wired in this build",
            FailureCode::TerminalNotAuthenticated => {
                "terminal authentication is not wired in this build"
            }
            FailureCode::UnsupportedMode => "the verification mode is not supported",
            FailureCode::AuthenticationUnavailable => {
                "an authentication dependency was unavailable; no verdict"
            }
            FailureCode::VerifierIdentityMismatch => {
                "the verifier's own frozen key or parameters failed their identity check; no verdict"
            }
            FailureCode::InternalVerifierError => "internal verifier error; no verdict",
        };
        format!("{text}{at}")
    }
}

impl fmt::Display for VerificationFailure {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{} ({})", self.code, self.research_code())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn research_codes_match_the_contract_names() {
        let at = VerificationFailure::at_step;
        assert_eq!(
            at(FailureCode::InvalidStepProof, 1).research_code(),
            "DA_PROOF(1)"
        );
        assert_eq!(
            at(FailureCode::BrokenContinuity, 0).research_code(),
            "DA_JOIN(0)"
        );
        assert_eq!(
            at(FailureCode::NonCanonicalProof, 2).research_code(),
            "DA_NONCANONICAL_PROOF(2)"
        );
        assert_eq!(
            VerificationFailure::non_canonical(FieldRole::NfOld, Some(2)).research_code(),
            "DA_NONCANONICAL(2)"
        );
        assert_eq!(
            VerificationFailure::non_canonical(FieldRole::RootCmx, None).research_code(),
            "ROOT_MALFORMED"
        );
        assert_eq!(
            VerificationFailure::new(FailureCode::ForeignVerifier).research_code(),
            "DA_FOREIGN_VK"
        );
        assert_eq!(
            VerificationFailure::layer_b(
                FailureCode::RootAuthenticationFailed,
                "ROOT_POLICY_ISSUER"
            )
            .research_code(),
            "ROOT_POLICY_ISSUER"
        );
    }

    #[test]
    fn no_failure_code_maps_to_a_success_status() {
        use FailureCode::*;
        let all = [
            InvalidEncoding,
            UnsupportedVersion,
            NonCanonicalField,
            EmptyLineage,
            TooManySteps,
            ForeignVerifier,
            NonCanonicalProof,
            InvalidStepProof,
            BrokenContinuity,
            RootMismatch,
            TerminalMismatch,
            ChainContextMissing,
            ChainRefMalformed,
            ChainRefUnsupportedVersion,
            ChainContextRejected,
            RootAuthenticationFailed,
            TerminalAuthenticationFailed,
            TerminalUnsupported,
            ReferenceNotFinal,
            ReferenceInvalidated,
            FinalityViolation,
            CheckpointedHistory,
            ContextMismatch,
            ChainContextNotAuthenticated,
            RootNotAuthenticated,
            TerminalNotAuthenticated,
            UnsupportedMode,
            AuthenticationUnavailable,
            VerifierIdentityMismatch,
            InternalVerifierError,
        ];
        for code in all {
            let status = code.status();
            assert!(!status.is_rooted_lineage_verified(), "{code}");
            assert_ne!(status, LineageStatus::LineageProofValid, "{code}");
        }
    }
}
