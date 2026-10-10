//! Chain-reference lifecycle and finality boundary (contract §C10, §D, §E).
//!
//! Phase 1 defines the states and results only. The tracker that persists
//! reference states across views, and the Depth/Checkpoint/Hybrid classifier,
//! are Layer-B work (M7) for Phase 2. The types are fixed now so that wiring
//! them does not change the public result model.

use core::fmt;

use crate::auth::Seal;
use crate::verdict::VerificationFailure;

/// How the verifier's chain history was committed (contract §D, §E).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum HistoryMode {
    /// FV: the verifier's node fully validated every block above genesis.
    FullValidation,
    /// CP: history up to `checkpoint_height` was committed through the
    /// checkpoint path. A `VERIFIED` result in this mode relies on CKPT-R.
    CheckpointTrust {
        /// Highest checkpoint-committed height `c`.
        checkpoint_height: u32,
    },
}

impl fmt::Display for HistoryMode {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::FullValidation => f.write_str("FV"),
            Self::CheckpointTrust { checkpoint_height } => write!(f, "CP(c={checkpoint_height})"),
        }
    }
}

/// State of one `chain_ref` as the verifier's tracker sees it (research
/// `RefState`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ReferenceState {
    /// Not on the verifier's selected chain and never seen.
    Unknown,
    /// On the selected chain, not yet final under policy.
    Accepted,
    /// Final under policy (Depth `K`, trusted checkpoint, or either).
    Finalized,
    /// Seen `Accepted`, then reorganized away.
    Invalidated,
    /// Seen `Finalized`, then reorganized away. Operator alarm.
    FinalityViolation,
}

impl ReferenceState {
    /// Stable wire name.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Unknown => "UNKNOWN",
            Self::Accepted => "ACCEPTED",
            Self::Finalized => "FINALIZED",
            Self::Invalidated => "INVALIDATED",
            Self::FinalityViolation => "FINALITY_VIOLATION",
        }
    }
}

/// A reference that is final under the verifier's policy. Only this crate can
/// construct one.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct FinalizedReference {
    history_mode: HistoryMode,
    depth: u32,
    _seal: Seal,
}

impl FinalizedReference {
    #[cfg_attr(not(test), allow(dead_code))]
    pub(crate) const fn new(history_mode: HistoryMode, depth: u32) -> Self {
        Self {
            history_mode,
            depth,
            _seal: Seal::new(),
        }
    }

    /// History mode the finality decision rests on.
    #[must_use]
    pub const fn history_mode(&self) -> HistoryMode {
        self.history_mode
    }

    /// `tip − height` at evaluation time.
    #[must_use]
    pub const fn depth(&self) -> u32 {
        self.depth
    }
}

/// Outcome of the finality and policy step (CR-7 … CR-9).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[must_use = "a finality result must be checked"]
pub enum FinalityResult {
    /// Final under policy.
    Finalized(FinalizedReference),
    /// On the selected chain, not yet final: `PENDING_FINALITY`.
    NotFinal,
    /// Reorganized away after it was seen: `INVALIDATED_BY_REORG`.
    Invalidated {
        /// The reference had been `Finalized` (alarm).
        finality_violation: bool,
    },
    /// Checkpoint-committed history with CP mode off:
    /// `CHECKPOINT_TRUST_REQUIRED`.
    CheckpointTrustRequired,
    /// A policy rejection such as `EXPIRED`.
    Rejected(VerificationFailure),
    /// The dependency is not available.
    Unavailable(crate::auth::UnavailableReason),
    /// The verifier could not decide (for example `CHECKPOINT_CONFLICT`).
    InternalError(VerificationFailure),
}
