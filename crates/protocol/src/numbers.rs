//! Canonical numeric protocol values.
//!
//! Every numeric field of `TradeCommitmentV1` is range-constrained to 64 bits
//! by the frozen circuits (`Num2Bits(64)`), so each is a distinct newtype over
//! `u64` rather than an interchangeable integer.

use core::fmt;

use crate::error::{ProtocolError, Result, TimestampProblem};

/// An amount of a ZSA, in raw asset units.
///
/// The frozen commitment range-constrains this to an unsigned 64-bit integer.
/// Decimals and display scaling are application concerns outside the
/// commitment.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct TradeAmount(u64);

/// An amount of native ZEC, in zatoshis.
///
/// The frozen commitment range-constrains the matcher fee to an unsigned
/// 64-bit integer. The fee is an application-agreed matcher payment, not a
/// consensus-level protocol tax.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct ZatoshiAmount(u64);

/// An application-assigned trade nonce.
///
/// The nonce distinguishes otherwise identical intents. Its deterministic
/// representation is the unsigned 64-bit integer that the circuits hash;
/// uniqueness and single use are matcher responsibilities, not commitment
/// properties.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct TradeNonce(u64);

/// A monotonic version counter for a signed-root envelope.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct RootVersion(u64);

/// A point in time, in unsigned Unix seconds.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct UnixSeconds(u64);

/// The committed expiry of a trade, in unsigned Unix seconds.
///
/// A trade is expired once the current time is strictly greater than this
/// value, so the expiry second itself is still usable. The eligibility circuit
/// additionally requires `credentialExpiry >= expiry`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct TradeExpiry(UnixSeconds);

macro_rules! u64_newtype {
    ($name:ident) => {
        impl $name {
            /// Wraps an unsigned 64-bit value.
            #[must_use]
            pub const fn new(value: u64) -> Self {
                Self(value)
            }

            /// Returns the underlying unsigned 64-bit value.
            #[must_use]
            pub const fn get(self) -> u64 {
                self.0
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                write!(f, "{}", self.0)
            }
        }
    };
}

u64_newtype!(TradeAmount);
u64_newtype!(ZatoshiAmount);
u64_newtype!(TradeNonce);
u64_newtype!(RootVersion);
u64_newtype!(UnixSeconds);

impl RootVersion {
    /// The lowest version a signed-root envelope may carry.
    pub const MIN: Self = Self(1);

    /// Reports whether this version is at least [`RootVersion::MIN`].
    #[must_use]
    pub const fn is_valid(self) -> bool {
        self.0 >= Self::MIN.0
    }
}

impl UnixSeconds {
    /// Adds a duration in seconds without wrapping.
    ///
    /// # Errors
    ///
    /// Returns [`TimestampProblem::Overflow`] if the sum exceeds `u64::MAX`.
    pub fn checked_add_seconds(self, seconds: u64) -> Result<Self> {
        self.0
            .checked_add(seconds)
            .map(Self)
            .ok_or(ProtocolError::InvalidTimestamp {
                reason: TimestampProblem::Overflow,
            })
    }

    /// Returns the number of seconds from `self` to `later`, or `None` if
    /// `later` precedes `self`.
    #[must_use]
    pub const fn seconds_until(self, later: Self) -> Option<u64> {
        later.0.checked_sub(self.0)
    }
}

impl TradeExpiry {
    /// Wraps an expiry given in Unix seconds.
    #[must_use]
    pub const fn new(seconds: u64) -> Self {
        Self(UnixSeconds::new(seconds))
    }

    /// Returns the expiry instant.
    #[must_use]
    pub const fn instant(self) -> UnixSeconds {
        self.0
    }

    /// Returns the expiry as unsigned Unix seconds.
    #[must_use]
    pub const fn get(self) -> u64 {
        self.0.get()
    }

    /// Reports whether the trade is expired at `now`.
    #[must_use]
    pub const fn is_expired_at(self, now: UnixSeconds) -> bool {
        now.get() > self.0.get()
    }

    /// Rejects the trade if it is expired at `now`.
    ///
    /// # Errors
    ///
    /// Returns [`ProtocolError::ExpiredTrade`] when `now` is strictly after the
    /// committed expiry.
    pub fn ensure_unexpired_at(self, now: UnixSeconds) -> Result<()> {
        if self.is_expired_at(now) {
            return Err(ProtocolError::ExpiredTrade {
                expiry: self.get(),
                now: now.get(),
            });
        }
        Ok(())
    }
}

impl fmt::Display for TradeExpiry {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Phase 0G `trade.expiry`.
    const GOLDEN_EXPIRY: u64 = 2_000_000_000;

    #[test]
    fn expiry_boundary_is_inclusive() {
        let expiry = TradeExpiry::new(GOLDEN_EXPIRY);
        assert!(!expiry.is_expired_at(UnixSeconds::new(GOLDEN_EXPIRY - 1)));
        assert!(!expiry.is_expired_at(UnixSeconds::new(GOLDEN_EXPIRY)));
        assert!(expiry.is_expired_at(UnixSeconds::new(GOLDEN_EXPIRY + 1)));
        assert!(expiry
            .ensure_unexpired_at(UnixSeconds::new(GOLDEN_EXPIRY))
            .is_ok());
        assert_eq!(
            expiry.ensure_unexpired_at(UnixSeconds::new(GOLDEN_EXPIRY + 1)),
            Err(ProtocolError::ExpiredTrade {
                expiry: GOLDEN_EXPIRY,
                now: GOLDEN_EXPIRY + 1
            })
        );
    }

    #[test]
    fn timestamp_arithmetic_is_overflow_safe() {
        assert_eq!(
            UnixSeconds::new(u64::MAX).checked_add_seconds(1),
            Err(ProtocolError::InvalidTimestamp {
                reason: TimestampProblem::Overflow
            })
        );
        assert_eq!(
            UnixSeconds::new(10).checked_add_seconds(5),
            Ok(UnixSeconds::new(15))
        );
        assert_eq!(
            UnixSeconds::new(10).seconds_until(UnixSeconds::new(25)),
            Some(15)
        );
        assert_eq!(
            UnixSeconds::new(25).seconds_until(UnixSeconds::new(10)),
            None
        );
    }

    #[test]
    fn root_version_zero_is_invalid() {
        assert!(!RootVersion::new(0).is_valid());
        assert!(RootVersion::new(1).is_valid());
    }
}
