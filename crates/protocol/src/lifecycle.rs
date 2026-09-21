//! Matcher-side trade lifecycle states.
//!
//! These names and the happy-path order are frozen in `docs/architecture.md`.
//! Transition rules live in [`crate::replay`]; this module is the shared
//! vocabulary so protocol errors can name a state without depending on the
//! in-memory store.

use core::fmt;

/// Frozen matcher-side lifecycle states for one `TradeCommitment`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum TradeLifecycleState {
    /// The trade exists but has not passed current matcher verification.
    Created,
    /// All required current matcher verification has passed.
    Verified,
    /// A transaction candidate exists; the commitment is locked against
    /// concurrent construction.
    SettlementConstructed,
    /// One candidate was submitted; track its txid and outcome.
    Submitted,
    /// Configured chain confirmation condition passed.
    Confirmed,
    /// Terminal success; all future attempts are rejected.
    Consumed,
    /// Terminal policy rejection after expiry.
    Expired,
    /// A failed attempt that may be eligible to restart from `CREATED`.
    Failed,
}

impl TradeLifecycleState {
    /// Reports whether this state is terminal success.
    #[must_use]
    pub const fn is_consumed(self) -> bool {
        matches!(self, Self::Consumed)
    }

    /// Reports whether this state is terminal expiry.
    #[must_use]
    pub const fn is_expired(self) -> bool {
        matches!(self, Self::Expired)
    }

    /// Reports whether settlement-active work may still run.
    ///
    /// `CONSUMED` and `EXPIRED` are never settlement-active. `FAILED` is not
    /// active until a controlled retry returns it to `CREATED`.
    #[must_use]
    pub const fn is_settlement_active(self) -> bool {
        matches!(
            self,
            Self::Created
                | Self::Verified
                | Self::SettlementConstructed
                | Self::Submitted
                | Self::Confirmed
        )
    }
}

impl fmt::Display for TradeLifecycleState {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let text = match self {
            Self::Created => "CREATED",
            Self::Verified => "VERIFIED",
            Self::SettlementConstructed => "SETTLEMENT_CONSTRUCTED",
            Self::Submitted => "SUBMITTED",
            Self::Confirmed => "CONFIRMED",
            Self::Consumed => "CONSUMED",
            Self::Expired => "EXPIRED",
            Self::Failed => "FAILED",
        };
        f.write_str(text)
    }
}

/// Lifecycle events the in-memory store can apply.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum LifecycleEvent {
    /// Insert a new `CREATED` record.
    Create,
    /// `CREATED` → `VERIFIED`.
    Verify,
    /// `VERIFIED` → `SETTLEMENT_CONSTRUCTED` (compare-and-set lock).
    AcquireConstruction,
    /// `SETTLEMENT_CONSTRUCTED` → `SUBMITTED`.
    Submit,
    /// `SUBMITTED` → `CONFIRMED`.
    Confirm,
    /// `CONFIRMED` → `CONSUMED`.
    Consume,
    /// Record a failed or rejected attempt.
    Fail,
    /// Terminal policy rejection after expiry.
    Expire,
    /// Controlled `FAILED` → `CREATED` retry.
    Retry,
}

impl fmt::Display for LifecycleEvent {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let text = match self {
            Self::Create => "CREATE",
            Self::Verify => "VERIFY",
            Self::AcquireConstruction => "ACQUIRE_CONSTRUCTION",
            Self::Submit => "SUBMIT",
            Self::Confirm => "CONFIRM",
            Self::Consume => "CONSUME",
            Self::Fail => "FAIL",
            Self::Expire => "EXPIRE",
            Self::Retry => "RETRY",
        };
        f.write_str(text)
    }
}

/// Why a trade moved to `FAILED`.
///
/// Construction or submission failure does not consume the trade. Only
/// configured confirmation followed by `CONSUME` does.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum FailureReason {
    /// A verification or policy check failed before construction.
    VerificationRejected,
    /// Settlement construction failed before submission.
    ConstructionFailed,
    /// Submission failed or the candidate was rejected before confirmation.
    SubmissionFailed,
    /// Confirmation tracking failed without reaching the configured condition.
    ConfirmationFailed,
}

impl fmt::Display for FailureReason {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let text = match self {
            Self::VerificationRejected => "verification rejected",
            Self::ConstructionFailed => "construction failed",
            Self::SubmissionFailed => "submission failed",
            Self::ConfirmationFailed => "confirmation failed",
        };
        f.write_str(text)
    }
}

/// A 32-byte settlement transaction identifier.
///
/// This is a canonical identifier used by the replay model to reconcile a
/// prior submission before retry. It is not Zcash transaction construction.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct SettlementTxId([u8; 32]);

impl SettlementTxId {
    /// Wraps an exactly 32-byte identifier.
    #[must_use]
    pub const fn new(bytes: [u8; 32]) -> Self {
        Self(bytes)
    }

    /// Returns the identifier bytes.
    #[must_use]
    pub const fn as_bytes(&self) -> &[u8; 32] {
        &self.0
    }
}
