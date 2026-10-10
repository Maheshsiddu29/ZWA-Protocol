//! Layer-B authentication interfaces (contract §C, M3–M6).
//!
//! Three verifier-side dependencies decide what Design A cannot:
//!
//! - [`ChainContextAuthenticator`]: is `chain_ref` a block of the verifier's own
//!   selected chain with that Orchard root (CR-1 … CR-6), and is it final under
//!   policy (CR-7 … CR-9)?
//! - [`RootAuthenticator`]: is `root_cmx` an authorized issue note in that chain
//!   (R1 … R8)?
//! - [`TerminalAuthenticator`]: is `terminal_cmx` a ZSA Action output in that
//!   chain (T1 … T7)?
//!
//! Root and terminal authentication take an [`AuthenticatedChainContext`],
//! which only a chain-context authenticator can produce, so they cannot run
//! against an unauthenticated `chain_ref`.
//!
//! **No success can be fabricated outside this crate.** Every success value
//! carries a [`Seal`] whose constructor is crate-private. Code outside the
//! crate can implement these traits, but its implementations can only return
//! failures or `Unavailable`. Phase 1 ships only the `NotWired*` adapters,
//! which report `Unavailable(NotWired)`; no adapter in this build can report an
//! authentication.
//!
//! Outside the crate, a success value cannot be built, neither through its
//! constructor nor as a struct literal:
//!
//! ```compile_fail
//! let _ = zwa_lineage::AuthenticatedRoot::new(1, 3, [0u8; 32]);
//! ```
//!
//! ```compile_fail
//! let _ = zwa_lineage::AuthenticatedTerminal::new(4, 25);
//! ```
//!
//! ```compile_fail
//! let _ = zwa_lineage::FinalizedReference::new(zwa_lineage::HistoryMode::FullValidation, 3);
//! ```
//!
//! ```compile_fail
//! let _ = zwa_lineage::auth::Seal(());
//! ```
//!
//! whereas failures can be reported freely:
//!
//! ```
//! use zwa_lineage::{FailureCode, RootAuthenticationResult, VerificationFailure};
//! let _ = RootAuthenticationResult::Rejected(VerificationFailure::layer_b(
//!     FailureCode::RootAuthenticationFailed,
//!     "ROOT_POLICY_ISSUER",
//! ));
//! ```

use core::fmt;

use crate::chain_ref::ChainRef;
use crate::field::CanonicalCmx;
use crate::finality::{FinalityResult, HistoryMode};
use crate::verdict::VerificationFailure;

/// Proof that a success value was produced inside this crate.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct Seal(());

impl Seal {
    #[cfg_attr(not(test), allow(dead_code))]
    pub(crate) const fn new() -> Self {
        Self(())
    }
}

/// Why an authentication dependency gave no answer.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum UnavailableReason {
    /// The dependency is not wired in this build: `PENDING_AUTHENTICATION`.
    NotWired,
    /// The verifier's chain view is missing or failed validation.
    ViewUnavailable,
    /// The dependency timed out.
    Timeout,
    /// The verifier's node is unreachable or aborted (FRZ-R5).
    NodeAborted,
}

impl fmt::Display for UnavailableReason {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::NotWired => "not wired in this build",
            Self::ViewUnavailable => "chain view unavailable",
            Self::Timeout => "timed out",
            Self::NodeAborted => "node unreachable or aborted",
        })
    }
}

/// A `chain_ref` the verifier's own view confirmed (CR-1 … CR-6).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct AuthenticatedChainContext {
    chain_ref: ChainRef,
    tip: u32,
    history_mode: HistoryMode,
    _seal: Seal,
}

impl AuthenticatedChainContext {
    #[cfg_attr(not(test), allow(dead_code))]
    pub(crate) const fn new(chain_ref: ChainRef, tip: u32, history_mode: HistoryMode) -> Self {
        Self {
            chain_ref,
            tip,
            history_mode,
            _seal: Seal::new(),
        }
    }

    /// The confirmed reference.
    #[must_use]
    pub const fn chain_ref(&self) -> &ChainRef {
        &self.chain_ref
    }

    /// The view's tip height.
    #[must_use]
    pub const fn tip(&self) -> u32 {
        self.tip
    }

    /// How the view's history was committed.
    #[must_use]
    pub const fn history_mode(&self) -> HistoryMode {
        self.history_mode
    }
}

/// Outcome of chain-context authentication.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[must_use = "a chain-context result must be checked"]
pub enum ChainIdentityResult {
    /// The reference names a block of the selected chain with that root.
    Authenticated(AuthenticatedChainContext),
    /// The reference was seen and then reorganized away.
    Invalidated {
        /// The reference had been `Finalized` (alarm).
        finality_violation: bool,
    },
    /// `NETWORK`, `UNKNOWN`, `UNKNOWN_FUTURE`, `TREESTATE_MISMATCH`, …
    Rejected(VerificationFailure),
    /// The dependency gave no answer.
    Unavailable(UnavailableReason),
    /// The verifier could not decide.
    InternalError(VerificationFailure),
}

/// What an authenticated root establishes.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct AuthenticatedRoot {
    height: u32,
    position: u64,
    asset_base: [u8; 32],
    _seal: Seal,
}

impl AuthenticatedRoot {
    #[cfg_attr(not(test), allow(dead_code))]
    pub(crate) const fn new(height: u32, position: u64, asset_base: [u8; 32]) -> Self {
        Self {
            height,
            position,
            asset_base,
            _seal: Seal::new(),
        }
    }

    /// Height of the issuance block.
    #[must_use]
    pub const fn height(&self) -> u32 {
        self.height
    }

    /// Orchard tree position of the issue note.
    #[must_use]
    pub const fn position(&self) -> u64 {
        self.position
    }

    /// The authenticated AssetBase of the issuance (public: issue notes are).
    #[must_use]
    pub const fn asset_base(&self) -> &[u8; 32] {
        &self.asset_base
    }
}

/// Outcome of root authentication.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[must_use = "a root authentication result must be checked"]
pub enum RootAuthenticationResult {
    /// `root_cmx` is an authorized issue note of the chain context.
    Authenticated(AuthenticatedRoot),
    /// `ROOT_*`.
    Rejected(VerificationFailure),
    /// The dependency gave no answer.
    Unavailable(UnavailableReason),
    /// The verifier could not decide.
    InternalError(VerificationFailure),
}

/// What an authenticated terminal establishes.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct AuthenticatedTerminal {
    height: u32,
    position: u64,
    _seal: Seal,
}

impl AuthenticatedTerminal {
    #[cfg_attr(not(test), allow(dead_code))]
    pub(crate) const fn new(height: u32, position: u64) -> Self {
        Self {
            height,
            position,
            _seal: Seal::new(),
        }
    }

    /// Height of the terminal's block.
    #[must_use]
    pub const fn height(&self) -> u32 {
        self.height
    }

    /// Orchard tree position of the terminal output.
    #[must_use]
    pub const fn position(&self) -> u64 {
        self.position
    }
}

/// Outcome of terminal authentication.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[must_use = "a terminal authentication result must be checked"]
pub enum TerminalAuthenticationResult {
    /// `terminal_cmx` is a ZSA Action output of the chain context.
    Authenticated(AuthenticatedTerminal),
    /// `TERM_*` (a vanilla terminal is `TERMINAL_UNSUPPORTED`).
    Rejected(VerificationFailure),
    /// The dependency gave no answer.
    Unavailable(UnavailableReason),
    /// The verifier could not decide.
    InternalError(VerificationFailure),
}

/// Chain-context authentication against the verifier's own view.
pub trait ChainContextAuthenticator {
    /// CR-1 … CR-6: network, height, block hash, Orchard root.
    fn authenticate_chain_ref(&self, chain_ref: &ChainRef) -> ChainIdentityResult;

    /// CR-7 … CR-9: finality, freshness, checkpoint policy.
    fn evaluate_finality(&self, context: &AuthenticatedChainContext) -> FinalityResult;
}

/// `authenticateRoot(root_cmx, chain_context)`.
pub trait RootAuthenticator {
    /// R1 … R8 against an authenticated chain context.
    fn authenticate_root(
        &self,
        root_cmx: &CanonicalCmx,
        context: &AuthenticatedChainContext,
    ) -> RootAuthenticationResult;
}

/// `authenticateTerminal(terminal_cmx, chain_context)`.
pub trait TerminalAuthenticator {
    /// T1 … T7 against an authenticated chain context.
    fn authenticate_terminal(
        &self,
        terminal_cmx: &CanonicalCmx,
        context: &AuthenticatedChainContext,
    ) -> TerminalAuthenticationResult;
}

/// Phase 1 chain-context adapter: always `Unavailable(NotWired)`.
#[derive(Debug, Clone, Copy, Default)]
pub struct NotWiredChainContext;

impl ChainContextAuthenticator for NotWiredChainContext {
    fn authenticate_chain_ref(&self, _chain_ref: &ChainRef) -> ChainIdentityResult {
        ChainIdentityResult::Unavailable(UnavailableReason::NotWired)
    }

    fn evaluate_finality(&self, _context: &AuthenticatedChainContext) -> FinalityResult {
        FinalityResult::Unavailable(UnavailableReason::NotWired)
    }
}

/// Phase 1 root adapter: always `Unavailable(NotWired)`.
#[derive(Debug, Clone, Copy, Default)]
pub struct NotWiredRootAuthenticator;

impl RootAuthenticator for NotWiredRootAuthenticator {
    fn authenticate_root(
        &self,
        _root_cmx: &CanonicalCmx,
        _context: &AuthenticatedChainContext,
    ) -> RootAuthenticationResult {
        RootAuthenticationResult::Unavailable(UnavailableReason::NotWired)
    }
}

/// Phase 1 terminal adapter: always `Unavailable(NotWired)`.
#[derive(Debug, Clone, Copy, Default)]
pub struct NotWiredTerminalAuthenticator;

impl TerminalAuthenticator for NotWiredTerminalAuthenticator {
    fn authenticate_terminal(
        &self,
        _terminal_cmx: &CanonicalCmx,
        _context: &AuthenticatedChainContext,
    ) -> TerminalAuthenticationResult {
        TerminalAuthenticationResult::Unavailable(UnavailableReason::NotWired)
    }
}
