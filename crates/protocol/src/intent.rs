//! The canonical trade intent.

use crate::bytes::AssetBaseBytes;
use crate::error::Result;
use crate::numbers::{TradeAmount, TradeExpiry, TradeNonce, UnixSeconds};
use crate::values::{MatcherFee, PolicyRoot, RecipientCommitment};

/// The canonical set of values committed by `TradeCommitmentV1`.
///
/// The field set corresponds exactly to the frozen commitment: there is no
/// ticker, display name, market symbol, order-book field, slippage tolerance,
/// or multi-hop routing data, because none of those are committed. Anything not
/// listed here is outside the protocol commitment and belongs to the
/// application layer.
///
/// RFQ and matcher components must construct this type rather than defining
/// their own trade schema, and must obtain the commitment from
/// `zwa_commitments::trade_commitment_v1` rather than recomputing the Poseidon
/// staging themselves.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TradeIntent {
    /// Canonical `AssetBase` of the asset being offered.
    pub offered_asset: AssetBaseBytes,
    /// Offered amount, in raw asset units.
    pub offered_amount: TradeAmount,
    /// Canonical `AssetBase` of the asset being requested in payment.
    pub requested_asset: AssetBaseBytes,
    /// Requested amount, in raw asset units.
    pub requested_amount: TradeAmount,
    /// Commitment to the intended recipient of the offered asset.
    pub recipient_commitment: RecipientCommitment,
    /// Policy root the asset requires the recipient's credential to satisfy.
    pub policy_root: PolicyRoot,
    /// Native ZEC matcher fee amount and fee-receiver commitment.
    pub matcher_fee: MatcherFee,
    /// Application-assigned nonce distinguishing otherwise identical intents.
    pub nonce: TradeNonce,
    /// Expiry of the intent, in unsigned Unix seconds.
    pub expiry: TradeExpiry,
}

impl TradeIntent {
    /// Reports whether the intent is expired at `now`.
    #[must_use]
    pub const fn is_expired_at(&self, now: UnixSeconds) -> bool {
        self.expiry.is_expired_at(now)
    }

    /// Rejects the intent if it is expired at `now`.
    ///
    /// All other component values are validated by their own types at
    /// construction, so expiry is the only time-dependent check left.
    ///
    /// # Errors
    ///
    /// Returns [`crate::error::ProtocolError::ExpiredTrade`] when `now` is
    /// strictly after the committed expiry.
    pub fn ensure_unexpired_at(&self, now: UnixSeconds) -> Result<()> {
        self.expiry.ensure_unexpired_at(now)
    }
}
