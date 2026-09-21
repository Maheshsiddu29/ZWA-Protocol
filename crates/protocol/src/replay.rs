//! In-memory deterministic matcher-side replay state.
//!
//! One record is stored per [`TradeCommitment`]. Persistence, networking, and
//! distributed locks belong to the matcher milestone; this module is the
//! canonical transition table those components must implement.
//!
//! Happy path:
//!
//! ```text
//! CREATED → VERIFIED → SETTLEMENT_CONSTRUCTED → SUBMITTED → CONFIRMED → CONSUMED
//! ```
//!
//! `CONSUMED` is terminal success. A failed or rejected settlement attempt
//! moves to `FAILED` and never to `CONSUMED` unless confirmation actually
//! occurred. A controlled retry may move `FAILED` back to `VERIFIED` only
//! while the trade is unexpired, after reconciling any prior txid, and under
//! a bounded retry policy.

use std::collections::BTreeMap;

use crate::error::{ProtocolError, Result};
use crate::intent::TradeIntent;
use crate::lifecycle::{FailureReason, LifecycleEvent, SettlementTxId, TradeLifecycleState};
use crate::numbers::UnixSeconds;
use crate::values::TradeCommitment;

/// Default number of controlled retries after `FAILED`.
pub const DEFAULT_MAX_RETRIES: u32 = 3;

/// One lifecycle record keyed by `TradeCommitment`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TradeRecord {
    commitment: TradeCommitment,
    intent: TradeIntent,
    state: TradeLifecycleState,
    failure_reason: Option<FailureReason>,
    prior_txid: Option<SettlementTxId>,
    retry_count: u32,
}

impl TradeRecord {
    /// Trade commitment this record is keyed by.
    #[must_use]
    pub const fn commitment(self) -> TradeCommitment {
        self.commitment
    }

    /// Canonical intent stored at `CREATED`.
    #[must_use]
    pub const fn intent(self) -> TradeIntent {
        self.intent
    }

    /// Current lifecycle state.
    #[must_use]
    pub const fn state(self) -> TradeLifecycleState {
        self.state
    }

    /// Reason for the current or last `FAILED` state, if any.
    #[must_use]
    pub const fn failure_reason(self) -> Option<FailureReason> {
        self.failure_reason
    }

    /// Txid of the last submission, if one was recorded.
    #[must_use]
    pub const fn prior_txid(self) -> Option<SettlementTxId> {
        self.prior_txid
    }

    /// Number of successful controlled retries already consumed.
    #[must_use]
    pub const fn retry_count(self) -> u32 {
        self.retry_count
    }
}

/// Deterministic in-memory replay store.
///
/// Compare-and-set is sequential: the first `acquire_construction` call for a
/// `VERIFIED` commitment succeeds and every subsequent call is rejected until
/// the record leaves `SETTLEMENT_CONSTRUCTED`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReplayStore {
    records: BTreeMap<TradeCommitment, TradeRecord>,
    max_retries: u32,
}

impl Default for ReplayStore {
    fn default() -> Self {
        Self::new()
    }
}

impl ReplayStore {
    /// Builds a store with [`DEFAULT_MAX_RETRIES`].
    #[must_use]
    pub fn new() -> Self {
        Self::with_max_retries(DEFAULT_MAX_RETRIES)
    }

    /// Builds a store with an explicit retry bound.
    #[must_use]
    pub fn with_max_retries(max_retries: u32) -> Self {
        Self {
            records: BTreeMap::new(),
            max_retries,
        }
    }

    /// Configured retry bound.
    #[must_use]
    pub const fn max_retries(&self) -> u32 {
        self.max_retries
    }

    /// Returns the record for `commitment`, if any.
    #[must_use]
    pub fn get(&self, commitment: TradeCommitment) -> Option<&TradeRecord> {
        self.records.get(&commitment)
    }

    /// Returns the current state for `commitment`, if any.
    #[must_use]
    pub fn state(&self, commitment: TradeCommitment) -> Option<TradeLifecycleState> {
        self.records.get(&commitment).map(|record| record.state())
    }

    /// Inserts a `CREATED` record. Duplicate keys are rejected.
    ///
    /// # Errors
    ///
    /// Returns [`ProtocolError::AlreadyConsumed`] if the commitment was already
    /// consumed, or [`ProtocolError::InvalidStateTransition`] if a record
    /// already exists.
    pub fn create(
        &mut self,
        commitment: TradeCommitment,
        intent: TradeIntent,
    ) -> Result<TradeRecord> {
        if let Some(existing) = self.records.get(&commitment) {
            return Err(reject(existing.state, LifecycleEvent::Create));
        }
        let record = TradeRecord {
            commitment,
            intent,
            state: TradeLifecycleState::Created,
            failure_reason: None,
            prior_txid: None,
            retry_count: 0,
        };
        self.records.insert(commitment, record);
        Ok(record)
    }

    /// `CREATED` → `VERIFIED` if the trade is unexpired at `now`.
    ///
    /// An expired trade is moved to `EXPIRED` and rejected.
    ///
    /// # Errors
    ///
    /// Returns [`ProtocolError::ExpiredTrade`] when the intent is expired,
    /// [`ProtocolError::AlreadyConsumed`] when consumed, or
    /// [`ProtocolError::InvalidStateTransition`] for any other current state.
    pub fn verify(&mut self, commitment: TradeCommitment, now: UnixSeconds) -> Result<TradeRecord> {
        let record = self.record_mut(commitment)?;
        deny_consumed(record.state)?;
        if record.state != TradeLifecycleState::Created {
            return Err(reject(record.state, LifecycleEvent::Verify));
        }
        if record.intent.is_expired_at(now) {
            record.state = TradeLifecycleState::Expired;
            return Err(ProtocolError::ExpiredTrade {
                expiry: record.intent.expiry.get(),
                now: now.get(),
            });
        }
        record.state = TradeLifecycleState::Verified;
        record.failure_reason = None;
        Ok(*record)
    }

    /// `VERIFIED` → `SETTLEMENT_CONSTRUCTED`.
    ///
    /// This is the compare-and-set lock: only one caller may hold the active
    /// construction state for a commitment.
    ///
    /// # Errors
    ///
    /// Returns [`ProtocolError::AlreadyConsumed`] or
    /// [`ProtocolError::InvalidStateTransition`].
    pub fn acquire_construction(&mut self, commitment: TradeCommitment) -> Result<TradeRecord> {
        let record = self.record_mut(commitment)?;
        deny_consumed(record.state)?;
        if record.state != TradeLifecycleState::Verified {
            return Err(reject(record.state, LifecycleEvent::AcquireConstruction));
        }
        record.state = TradeLifecycleState::SettlementConstructed;
        Ok(*record)
    }

    /// `SETTLEMENT_CONSTRUCTED` → `SUBMITTED`, recording `txid`.
    ///
    /// # Errors
    ///
    /// Returns [`ProtocolError::AlreadyConsumed`] or
    /// [`ProtocolError::InvalidStateTransition`].
    pub fn submit(
        &mut self,
        commitment: TradeCommitment,
        txid: SettlementTxId,
    ) -> Result<TradeRecord> {
        let record = self.record_mut(commitment)?;
        deny_consumed(record.state)?;
        if record.state != TradeLifecycleState::SettlementConstructed {
            return Err(reject(record.state, LifecycleEvent::Submit));
        }
        record.state = TradeLifecycleState::Submitted;
        record.prior_txid = Some(txid);
        Ok(*record)
    }

    /// `SUBMITTED` → `CONFIRMED`.
    ///
    /// # Errors
    ///
    /// Returns [`ProtocolError::AlreadyConsumed`] or
    /// [`ProtocolError::InvalidStateTransition`].
    pub fn confirm(&mut self, commitment: TradeCommitment) -> Result<TradeRecord> {
        let record = self.record_mut(commitment)?;
        deny_consumed(record.state)?;
        if record.state != TradeLifecycleState::Submitted {
            return Err(reject(record.state, LifecycleEvent::Confirm));
        }
        record.state = TradeLifecycleState::Confirmed;
        Ok(*record)
    }

    /// `CONFIRMED` → `CONSUMED`.
    ///
    /// Construction, submission, or confirmation failure must not call this.
    /// Only configured confirmation reaches `CONSUMED`.
    ///
    /// # Errors
    ///
    /// Returns [`ProtocolError::AlreadyConsumed`] or
    /// [`ProtocolError::InvalidStateTransition`].
    pub fn consume(&mut self, commitment: TradeCommitment) -> Result<TradeRecord> {
        let record = self.record_mut(commitment)?;
        deny_consumed(record.state)?;
        if record.state != TradeLifecycleState::Confirmed {
            return Err(reject(record.state, LifecycleEvent::Consume));
        }
        record.state = TradeLifecycleState::Consumed;
        Ok(*record)
    }

    /// Records a failed or rejected attempt.
    ///
    /// Allowed from `CREATED`, `VERIFIED`, `SETTLEMENT_CONSTRUCTED`, and
    /// `SUBMITTED`. A `SUBMITTED` failure keeps the prior txid so a retry can
    /// reconcile it. The record never becomes `CONSUMED`.
    ///
    /// # Errors
    ///
    /// Returns [`ProtocolError::AlreadyConsumed`] or
    /// [`ProtocolError::InvalidStateTransition`].
    pub fn fail(
        &mut self,
        commitment: TradeCommitment,
        reason: FailureReason,
    ) -> Result<TradeRecord> {
        let record = self.record_mut(commitment)?;
        deny_consumed(record.state)?;
        if !matches!(
            record.state,
            TradeLifecycleState::Created
                | TradeLifecycleState::Verified
                | TradeLifecycleState::SettlementConstructed
                | TradeLifecycleState::Submitted
        ) {
            return Err(reject(record.state, LifecycleEvent::Fail));
        }
        if record.state != TradeLifecycleState::Submitted {
            record.prior_txid = None;
        }
        record.state = TradeLifecycleState::Failed;
        record.failure_reason = Some(reason);
        Ok(*record)
    }

    /// Terminal policy rejection after expiry.
    ///
    /// Allowed from `CREATED`, `VERIFIED`, `SETTLEMENT_CONSTRUCTED`, and
    /// `FAILED` when `now` is strictly after the committed expiry.
    /// `SUBMITTED` must be confirmed or failed first so an in-flight txid is
    /// not dropped.
    ///
    /// # Errors
    ///
    /// Returns [`ProtocolError::AlreadyConsumed`],
    /// [`ProtocolError::InvalidStateTransition`], or
    /// [`ProtocolError::ExpiredTrade`] is not used here: a not-yet-expired
    /// trade cannot be expired.
    pub fn expire(&mut self, commitment: TradeCommitment, now: UnixSeconds) -> Result<TradeRecord> {
        let record = self.record_mut(commitment)?;
        deny_consumed(record.state)?;
        if !matches!(
            record.state,
            TradeLifecycleState::Created
                | TradeLifecycleState::Verified
                | TradeLifecycleState::SettlementConstructed
                | TradeLifecycleState::Failed
        ) {
            return Err(reject(record.state, LifecycleEvent::Expire));
        }
        if !record.intent.is_expired_at(now) {
            return Err(reject(record.state, LifecycleEvent::Expire));
        }
        record.state = TradeLifecycleState::Expired;
        Ok(*record)
    }

    /// Controlled `FAILED` → `VERIFIED` retry.
    ///
    /// Requires the trade to be unexpired, remaining retry budget, and an
    /// exact acknowledgement of any prior submitted txid.
    ///
    /// # Errors
    ///
    /// Returns [`ProtocolError::AlreadyConsumed`],
    /// [`ProtocolError::ExpiredTrade`],
    /// [`ProtocolError::RetryBudgetExhausted`],
    /// [`ProtocolError::UnreconciledPriorSubmission`], or
    /// [`ProtocolError::InvalidStateTransition`].
    pub fn retry_after_failure(
        &mut self,
        commitment: TradeCommitment,
        acknowledged_txid: Option<SettlementTxId>,
        now: UnixSeconds,
    ) -> Result<TradeRecord> {
        let max_retries = self.max_retries;
        let record = self.record_mut(commitment)?;
        deny_consumed(record.state)?;
        if record.state != TradeLifecycleState::Failed {
            return Err(reject(record.state, LifecycleEvent::Retry));
        }
        if record.intent.is_expired_at(now) {
            record.state = TradeLifecycleState::Expired;
            return Err(ProtocolError::ExpiredTrade {
                expiry: record.intent.expiry.get(),
                now: now.get(),
            });
        }
        if record.retry_count >= max_retries {
            return Err(ProtocolError::RetryBudgetExhausted {
                attempts: record.retry_count,
                max: max_retries,
            });
        }
        if record.prior_txid != acknowledged_txid {
            return Err(ProtocolError::UnreconciledPriorSubmission);
        }
        record.state = TradeLifecycleState::Verified;
        record.failure_reason = None;
        record.prior_txid = None;
        record.retry_count += 1;
        Ok(*record)
    }

    fn record_mut(&mut self, commitment: TradeCommitment) -> Result<&mut TradeRecord> {
        self.records
            .get_mut(&commitment)
            .ok_or(ProtocolError::UnknownTrade)
    }
}

fn deny_consumed(state: TradeLifecycleState) -> Result<()> {
    if state.is_consumed() {
        Err(ProtocolError::AlreadyConsumed)
    } else {
        Ok(())
    }
}

fn reject(from: TradeLifecycleState, attempted: LifecycleEvent) -> ProtocolError {
    if from.is_consumed() {
        ProtocolError::AlreadyConsumed
    } else {
        ProtocolError::InvalidStateTransition { from, attempted }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        AssetBaseBytes, FieldElement, MatcherFee, PolicyRoot, RecipientCommitment, TradeAmount,
        TradeExpiry, TradeNonce, ZatoshiAmount,
    };

    /// Phase 0G trade expiry.
    const GOLDEN_EXPIRY: u64 = 2_000_000_000;

    fn intent(expiry: u64) -> TradeIntent {
        TradeIntent {
            offered_asset: AssetBaseBytes::new([0x48; 32]),
            offered_amount: TradeAmount::new(10),
            requested_asset: AssetBaseBytes::new([0xa7; 32]),
            requested_amount: TradeAmount::new(6),
            recipient_commitment: RecipientCommitment::new(FieldElement::from_u64(1)),
            policy_root: PolicyRoot::new(FieldElement::from_u64(2)),
            matcher_fee: MatcherFee::new(
                ZatoshiAmount::new(5),
                RecipientCommitment::new(FieldElement::from_u64(3)),
            ),
            nonce: TradeNonce::new(7001),
            expiry: TradeExpiry::new(expiry),
        }
    }

    fn commitment(tag: u64) -> TradeCommitment {
        TradeCommitment::new(FieldElement::from_u64(tag))
    }

    fn txid(tag: u8) -> SettlementTxId {
        SettlementTxId::new([tag; 32])
    }

    fn now() -> UnixSeconds {
        UnixSeconds::new(GOLDEN_EXPIRY - 1)
    }

    fn happy_path_to(store: &mut ReplayStore, key: TradeCommitment, until: TradeLifecycleState) {
        store.create(key, intent(GOLDEN_EXPIRY)).unwrap();
        if until == TradeLifecycleState::Created {
            return;
        }
        store.verify(key, now()).unwrap();
        if until == TradeLifecycleState::Verified {
            return;
        }
        store.acquire_construction(key).unwrap();
        if until == TradeLifecycleState::SettlementConstructed {
            return;
        }
        store.submit(key, txid(1)).unwrap();
        if until == TradeLifecycleState::Submitted {
            return;
        }
        store.confirm(key).unwrap();
        if until == TradeLifecycleState::Confirmed {
            return;
        }
        store.consume(key).unwrap();
    }

    #[test]
    fn happy_path_lifecycle_reaches_consumed() {
        let mut store = ReplayStore::new();
        let key = commitment(1);
        happy_path_to(&mut store, key, TradeLifecycleState::Consumed);
        assert_eq!(store.state(key), Some(TradeLifecycleState::Consumed));
        assert!(store.get(key).unwrap().state().is_consumed());
    }

    #[test]
    fn duplicate_transition_is_rejected() {
        let mut store = ReplayStore::new();
        let key = commitment(2);
        store.create(key, intent(GOLDEN_EXPIRY)).unwrap();
        assert_eq!(
            store.create(key, intent(GOLDEN_EXPIRY)),
            Err(ProtocolError::InvalidStateTransition {
                from: TradeLifecycleState::Created,
                attempted: LifecycleEvent::Create
            })
        );
        store.verify(key, now()).unwrap();
        assert_eq!(
            store.verify(key, now()),
            Err(ProtocolError::InvalidStateTransition {
                from: TradeLifecycleState::Verified,
                attempted: LifecycleEvent::Verify
            })
        );
    }

    #[test]
    fn illegal_state_jump_is_rejected() {
        let mut store = ReplayStore::new();
        let key = commitment(3);
        store.create(key, intent(GOLDEN_EXPIRY)).unwrap();
        assert_eq!(
            store.submit(key, txid(1)),
            Err(ProtocolError::InvalidStateTransition {
                from: TradeLifecycleState::Created,
                attempted: LifecycleEvent::Submit
            })
        );
        assert_eq!(
            store.consume(key),
            Err(ProtocolError::InvalidStateTransition {
                from: TradeLifecycleState::Created,
                attempted: LifecycleEvent::Consume
            })
        );
        assert_eq!(
            store.confirm(key),
            Err(ProtocolError::InvalidStateTransition {
                from: TradeLifecycleState::Created,
                attempted: LifecycleEvent::Confirm
            })
        );
    }

    #[test]
    fn consumed_trade_cannot_reenter_settlement_active_states() {
        let mut store = ReplayStore::new();
        let key = commitment(4);
        happy_path_to(&mut store, key, TradeLifecycleState::Consumed);
        assert_eq!(
            store.verify(key, now()),
            Err(ProtocolError::AlreadyConsumed)
        );
        assert_eq!(
            store.acquire_construction(key),
            Err(ProtocolError::AlreadyConsumed)
        );
        assert_eq!(
            store.submit(key, txid(9)),
            Err(ProtocolError::AlreadyConsumed)
        );
        assert_eq!(store.confirm(key), Err(ProtocolError::AlreadyConsumed));
        assert_eq!(store.consume(key), Err(ProtocolError::AlreadyConsumed));
        assert_eq!(
            store.fail(key, FailureReason::SubmissionFailed),
            Err(ProtocolError::AlreadyConsumed)
        );
        assert_eq!(
            store.retry_after_failure(key, None, now()),
            Err(ProtocolError::AlreadyConsumed)
        );
        assert_eq!(
            store.create(key, intent(GOLDEN_EXPIRY)),
            Err(ProtocolError::AlreadyConsumed)
        );
    }

    #[test]
    fn expired_trade_verification_is_rejected() {
        let mut store = ReplayStore::new();
        let key = commitment(5);
        store.create(key, intent(GOLDEN_EXPIRY)).unwrap();
        assert_eq!(
            store.verify(key, UnixSeconds::new(GOLDEN_EXPIRY + 1)),
            Err(ProtocolError::ExpiredTrade {
                expiry: GOLDEN_EXPIRY,
                now: GOLDEN_EXPIRY + 1
            })
        );
        assert_eq!(store.state(key), Some(TradeLifecycleState::Expired));
        assert_eq!(
            store.verify(key, now()),
            Err(ProtocolError::InvalidStateTransition {
                from: TradeLifecycleState::Expired,
                attempted: LifecycleEvent::Verify
            })
        );
    }

    #[test]
    fn controlled_retry_after_submission_failure() {
        let mut store = ReplayStore::new();
        let key = commitment(6);
        happy_path_to(&mut store, key, TradeLifecycleState::Submitted);
        store.fail(key, FailureReason::SubmissionFailed).unwrap();
        assert_eq!(store.state(key), Some(TradeLifecycleState::Failed));
        assert_eq!(
            store.get(key).unwrap().prior_txid(),
            Some(txid(1)),
            "a failed submission must keep the txid for reconciliation"
        );

        assert_eq!(
            store.retry_after_failure(key, None, now()),
            Err(ProtocolError::UnreconciledPriorSubmission)
        );
        assert_eq!(
            store.retry_after_failure(key, Some(txid(2)), now()),
            Err(ProtocolError::UnreconciledPriorSubmission)
        );

        let retried = store
            .retry_after_failure(key, Some(txid(1)), now())
            .unwrap();
        assert_eq!(retried.state(), TradeLifecycleState::Verified);
        assert_eq!(retried.retry_count(), 1);
        assert_eq!(retried.prior_txid(), None);
        assert!(retried.failure_reason().is_none());

        // Construction failure never consumes the trade.
        store.acquire_construction(key).unwrap();
        store.fail(key, FailureReason::ConstructionFailed).unwrap();
        assert_ne!(store.state(key), Some(TradeLifecycleState::Consumed));
        store.retry_after_failure(key, None, now()).unwrap();
        assert_eq!(store.state(key), Some(TradeLifecycleState::Verified));
    }

    #[test]
    fn only_one_caller_may_acquire_construction() {
        let mut store = ReplayStore::new();
        let key = commitment(7);
        happy_path_to(&mut store, key, TradeLifecycleState::Verified);
        assert!(store.acquire_construction(key).is_ok());
        assert_eq!(
            store.acquire_construction(key),
            Err(ProtocolError::InvalidStateTransition {
                from: TradeLifecycleState::SettlementConstructed,
                attempted: LifecycleEvent::AcquireConstruction
            })
        );
        assert_eq!(
            store.state(key),
            Some(TradeLifecycleState::SettlementConstructed)
        );
    }

    #[test]
    fn retry_budget_is_bounded_and_expired_retry_is_rejected() {
        let mut store = ReplayStore::with_max_retries(1);
        let key = commitment(8);
        store.create(key, intent(GOLDEN_EXPIRY)).unwrap();
        store.verify(key, now()).unwrap();
        store
            .fail(key, FailureReason::VerificationRejected)
            .unwrap();
        store.retry_after_failure(key, None, now()).unwrap();
        store
            .fail(key, FailureReason::VerificationRejected)
            .unwrap();
        assert_eq!(
            store.retry_after_failure(key, None, now()),
            Err(ProtocolError::RetryBudgetExhausted {
                attempts: 1,
                max: 1
            })
        );
        assert_eq!(store.state(key), Some(TradeLifecycleState::Failed));

        let expired_key = commitment(9);
        store.create(expired_key, intent(GOLDEN_EXPIRY)).unwrap();
        store.verify(expired_key, now()).unwrap();
        store
            .fail(expired_key, FailureReason::VerificationRejected)
            .unwrap();
        assert_eq!(
            store.retry_after_failure(expired_key, None, UnixSeconds::new(GOLDEN_EXPIRY + 1)),
            Err(ProtocolError::ExpiredTrade {
                expiry: GOLDEN_EXPIRY,
                now: GOLDEN_EXPIRY + 1
            })
        );
        assert_eq!(store.state(expired_key), Some(TradeLifecycleState::Expired));
    }

    #[test]
    fn unknown_commitment_is_rejected() {
        let mut store = ReplayStore::new();
        assert_eq!(
            store.verify(commitment(99), now()),
            Err(ProtocolError::UnknownTrade)
        );
    }
}
