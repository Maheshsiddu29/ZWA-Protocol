//! Typed protocol errors.
//!
//! Protocol invariants never surface as untyped strings: every rejection a
//! caller has to branch on is a distinct [`ProtocolError`] variant.

use core::fmt;

/// Every way a canonical ZWA protocol value, envelope, or state transition can
/// be rejected.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[non_exhaustive]
pub enum ProtocolError {
    /// A canonical `orchard::note::AssetBase` encoding was not exactly 32 bytes.
    #[error("AssetBase must be exactly 32 bytes, got {got}")]
    InvalidAssetBase {
        /// Length that was supplied.
        got: usize,
    },

    /// A raw Orchard payment address was not exactly 43 bytes.
    #[error("Orchard raw payment address must be exactly 43 bytes, got {got}")]
    InvalidReceiver {
        /// Length that was supplied.
        got: usize,
    },

    /// A value was not a canonical encoding of a BN254 scalar field element.
    #[error("invalid canonical field encoding: {reason}")]
    InvalidFieldEncoding {
        /// Why the encoding was rejected.
        reason: FieldEncodingProblem,
    },

    /// A limb exceeded the bit width the frozen circuits range-constrain it to.
    #[error("{limb} limb exceeds its {bits}-bit range constraint")]
    LimbOutOfRange {
        /// Which limb was out of range.
        limb: LimbKind,
        /// Bit width the frozen circuit constrains the limb to.
        bits: u32,
    },

    /// An amount was outside the range the frozen commitment permits.
    #[error("{field} must be an unsigned 64-bit value")]
    InvalidAmount {
        /// Which amount was rejected.
        field: AmountField,
    },

    /// A credential policy code exceeded the bit width the frozen eligibility
    /// circuit range-constrains it to.
    #[error("{code} exceeds its {bits}-bit range constraint")]
    CredentialCodeOutOfRange {
        /// Which code was rejected.
        code: CredentialCodeField,
        /// Bit width the frozen circuit constrains the code to.
        bits: u32,
    },

    /// A timestamp was malformed or a validity window was inconsistent.
    #[error("invalid timestamp: {reason}")]
    InvalidTimestamp {
        /// Why the timestamp or window was rejected.
        reason: TimestampProblem,
    },

    /// A trade was used strictly after its committed expiry second.
    #[error("trade expired at {expiry} and cannot be used at {now}")]
    ExpiredTrade {
        /// Committed expiry, in Unix seconds.
        expiry: u64,
        /// Verification time, in Unix seconds.
        now: u64,
    },

    /// A recomputed commitment did not equal the commitment that was presented.
    #[error("commitment mismatch: expected {expected}, recomputed {actual}")]
    CommitmentMismatch {
        /// Commitment the caller presented, as a canonical decimal string.
        expected: String,
        /// Commitment the protocol recomputed, as a canonical decimal string.
        actual: String,
    },

    /// Poseidon was asked for an arity unsupported by the Circom-compatible
    /// implementation.
    #[error("unsupported Poseidon arity {arity}")]
    UnsupportedPoseidonArity {
        /// Arity that was requested.
        arity: usize,
    },

    /// A signed-root envelope failed non-cryptographic structural checks.
    ///
    /// Passing this check produces a structurally valid envelope, never a
    /// cryptographically authenticated one.
    #[error("invalid root envelope: {reason}")]
    InvalidRootEnvelope {
        /// Why the envelope was rejected.
        reason: RootEnvelopeProblem,
    },

    /// A version field was outside the supported range.
    #[error("unsupported version {got}")]
    UnsupportedVersion {
        /// Version that was supplied.
        got: u64,
    },

    /// An opaque proof container was malformed.
    #[error("invalid proof encoding: {reason}")]
    InvalidProof {
        /// Why the proof container was rejected.
        reason: ProofProblem,
    },

    /// A matcher-side lifecycle transition is not in the allowed set.
    #[error("illegal trade lifecycle transition from {from} via {attempted}")]
    InvalidStateTransition {
        /// State the record was in.
        from: crate::lifecycle::TradeLifecycleState,
        /// Transition that was attempted.
        attempted: crate::lifecycle::LifecycleEvent,
    },

    /// A `TradeCommitment` in the `CONSUMED` state was presented again.
    #[error("trade commitment is already consumed and cannot be used again")]
    AlreadyConsumed,

    /// No lifecycle record exists for the supplied `TradeCommitment`.
    #[error("no lifecycle record for the supplied trade commitment")]
    UnknownTrade,

    /// A retry was requested without acknowledging the prior submitted txid.
    #[error("retry requires acknowledging the prior settlement txid")]
    UnreconciledPriorSubmission,

    /// The bounded retry policy has no remaining attempts.
    #[error("retry budget exhausted ({attempts} of {max})")]
    RetryBudgetExhausted {
        /// Successful retries already consumed.
        attempts: u32,
        /// Configured maximum retries.
        max: u32,
    },
}

/// Why a canonical field encoding was rejected.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FieldEncodingProblem {
    /// The big-endian byte string was not exactly 32 bytes.
    WrongLength,
    /// The integer was greater than or equal to the BN254 scalar modulus.
    NotReduced,
    /// The decimal string was empty or contained a non-digit character.
    NotDecimal,
    /// The decimal string denoted a value that does not fit in 256 bits.
    DecimalOverflow,
}

impl fmt::Display for FieldEncodingProblem {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let text = match self {
            Self::WrongLength => "big-endian encoding must be exactly 32 bytes",
            Self::NotReduced => "value is not reduced modulo the BN254 scalar field",
            Self::NotDecimal => "value is not a non-empty decimal string",
            Self::DecimalOverflow => "decimal value does not fit in 256 bits",
        };
        f.write_str(text)
    }
}

/// Which range-constrained limb was rejected.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LimbKind {
    /// Low 128-bit `AssetBase` limb.
    AssetBaseLo,
    /// High 128-bit `AssetBase` limb.
    AssetBaseHi,
    /// Orchard receiver limb 0, covering raw bytes 0..16.
    ReceiverLimb0,
    /// Orchard receiver limb 1, covering raw bytes 16..32.
    ReceiverLimb1,
    /// Orchard receiver limb 2, covering raw bytes 32..43.
    ReceiverLimb2,
}

impl fmt::Display for LimbKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let text = match self {
            Self::AssetBaseLo => "AssetBase lo",
            Self::AssetBaseHi => "AssetBase hi",
            Self::ReceiverLimb0 => "receiver limb 0",
            Self::ReceiverLimb1 => "receiver limb 1",
            Self::ReceiverLimb2 => "receiver limb 2",
        };
        f.write_str(text)
    }
}

/// Which range-constrained credential policy code was rejected.
///
/// The eligibility circuit constrains these more tightly than the 64-bit
/// commitment fields, so they have their own rejection reason.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CredentialCodeField {
    /// Investor class, constrained by `Num2Bits(8)`.
    InvestorClass,
    /// Jurisdiction, constrained by `Num2Bits(16)`.
    Jurisdiction,
}

impl fmt::Display for CredentialCodeField {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let text = match self {
            Self::InvestorClass => "investor class",
            Self::Jurisdiction => "jurisdiction",
        };
        f.write_str(text)
    }
}

/// Which committed amount was rejected.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AmountField {
    /// Offered asset amount, in raw asset units.
    OfferedAmount,
    /// Requested asset amount, in raw asset units.
    RequestedAmount,
    /// Native ZEC matcher fee, in zatoshis.
    MatcherFee,
}

impl fmt::Display for AmountField {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let text = match self {
            Self::OfferedAmount => "offered amount",
            Self::RequestedAmount => "requested amount",
            Self::MatcherFee => "matcher fee amount",
        };
        f.write_str(text)
    }
}

/// Why a timestamp or validity window was rejected.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TimestampProblem {
    /// `valid_from` was greater than `expires_at`.
    WindowInverted,
    /// A Unix-second arithmetic operation would have overflowed.
    Overflow,
}

impl fmt::Display for TimestampProblem {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let text = match self {
            Self::WindowInverted => "valid_from must be less than or equal to expires_at",
            Self::Overflow => "Unix-second arithmetic overflowed",
        };
        f.write_str(text)
    }
}

/// Why a signed-root envelope failed structural validation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RootEnvelopeProblem {
    /// The issuer or credential-authority identifier was empty.
    MissingAuthorityIdentifier,
    /// The identifier exceeded the container bound.
    IdentifierTooLong {
        /// Length that was supplied.
        got: usize,
        /// Maximum accepted length.
        max: usize,
    },
    /// Signature material was empty.
    SignatureMissing,
    /// Signature material exceeded the container bound.
    SignatureTooLong {
        /// Length that was supplied.
        got: usize,
        /// Maximum accepted length.
        max: usize,
    },
    /// Verification time is before `valid_from`.
    NotYetValid {
        /// Window start, in Unix seconds.
        valid_from: u64,
        /// Verification time, in Unix seconds.
        now: u64,
    },
    /// Verification time is after `expires_at`.
    Expired {
        /// Window end, in Unix seconds.
        expires_at: u64,
        /// Verification time, in Unix seconds.
        now: u64,
    },
}

impl fmt::Display for RootEnvelopeProblem {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::MissingAuthorityIdentifier => {
                f.write_str("issuer or credential-authority identifier is required")
            }
            Self::IdentifierTooLong { got, max } => {
                write!(f, "authority identifier is {got} bytes, max {max}")
            }
            Self::SignatureMissing => f.write_str("signature material must be non-empty"),
            Self::SignatureTooLong { got, max } => {
                write!(f, "signature material is {got} bytes, max {max}")
            }
            Self::NotYetValid { valid_from, now } => {
                write!(
                    f,
                    "envelope valid_from {valid_from} is after verification time {now}"
                )
            }
            Self::Expired { expires_at, now } => {
                write!(
                    f,
                    "envelope expired at {expires_at} and cannot be used at {now}"
                )
            }
        }
    }
}

/// Why an opaque proof container was rejected.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProofProblem {
    /// The container was empty.
    Empty,
    /// The container exceeded the size bound.
    TooLarge {
        /// Length that was supplied.
        got: usize,
        /// Maximum accepted length.
        max: usize,
    },
}

impl fmt::Display for ProofProblem {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Empty => f.write_str("proof bytes must be non-empty"),
            Self::TooLarge { got, max } => write!(f, "proof is {got} bytes, max {max}"),
        }
    }
}

/// Convenience alias for protocol fallibility.
pub type Result<T> = core::result::Result<T, ProtocolError>;
