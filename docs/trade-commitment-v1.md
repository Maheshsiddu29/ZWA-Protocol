# TradeCommitmentV1

## Status

This document freezes the Phase 0F/0G schema without redesign. All hashes below are Poseidon over BN254 field elements. Domain labels are encoded as positive big-endian integers.

## Inputs

| Field | Encoding and range | Visibility in the proofs |
|---|---|---|
| offered `AssetBase` | canonical 32 bytes; two 128-bit limbs | private |
| offered amount | unsigned 64-bit integer | private |
| requested `AssetBase` | canonical 32 bytes; two 128-bit limbs | private |
| requested amount | unsigned 64-bit integer | private |
| recipient commitment | BN254 scalar | private |
| policy root | BN254 scalar | private |
| native ZEC matcher-fee amount | unsigned 64-bit zatoshis | private |
| matcher-fee recipient commitment | BN254 scalar | private |
| nonce | unsigned 64-bit application nonce | private |
| expiry | unsigned 64-bit Unix time in seconds | private |

The resulting `tradeCommitment` is public. Phase 0 circuits also expose their respective Merkle root.

## Domain separators

Every tag is its ASCII label read as a positive big-endian integer. The tables
below are split by which component computes them, because the two groups have
different sources of truth.

### Implemented by the Phase 1 commitment engine

These are exactly the separators defined by `Domain` in
`crates/commitments/src/domain.rs`, and they cover the whole
`TradeCommitmentV1` and recipient-binding path.

| Label | Integer | Used by |
|---|---:|---|
| `ASSETV1` | 18387490596738609 | `AssetCommitment` for both assets |
| `FEEV1` | 301809882673 | `FeeCommitment` |
| `ZEC` | 5915971 | native-ZEC fee asset tag inside `FeeCommitment` |
| `TRDA_V1` | 23734351151715889 | `TradePartA` |
| `TRDB_V1` | 23734351168493105 | `TradePartB` |
| `TRDM_V1` | 23734351353042481 | `TradeMeta` |
| `TRADE_V1` | 6075990608753677873 | `TradeCommitmentV1` |
| `SUBJECT1` | 6004778564925477937 | `SubjectCommitment` |
| `RECEIVR1` | 5928218449365324337 | `ReceiverCommitment` |
| `RCPBIND1` | 5927669780177634353 | Phase 0G `recipientCommitment` |
| `FRCPTV1` | 19793697433736753 | `matcherFeeRecipientCommitment` |
| `RCPTV1` | 90449063990833 | Phase 0F trade `recipientCommitment` |

`ZEC` is a fee-asset tag, not an `AssetBase`.

`FRCPTV1` and `RCPTV1` are canonical-byte commitments rather than limb
commitments; see [Canonical-byte recipient commitments](#canonical-byte-recipient-commitments).
`RCPTV1` is retained because the frozen Phase 0F reference vector depends on it;
Phase 0G superseded it with `RCPBIND1`, which additionally binds the credential
subject.

### Circuit-side leaf separators

These are used by the Circom circuits and `circuits/shared/*.js` to build
issuance and credential leaves. The Phase 1 Rust `Domain` type deliberately does
not define them, because Phase 1 does not recompute those leaves; they are
listed here so the frozen constant table is complete.

| Label | Integer | Used by |
|---|---:|---|
| `ISSMETA1` | 5283658379176460593 | issuance leaf metadata |
| `ISSUEV1` | 20639290677876273 | issuance leaf |
| `CREDMETA` | 4851015908287927361 | credential leaf metadata |
| `CRED_V1` | 18949280892933681 | credential leaf |
| `ELIGPOL1` | 4993446657485917233 | eligibility policy leaf |

## Exact Poseidon staging

```text
OfferedAssetCommitment   = H(ASSETV1, offeredAssetHi, offeredAssetLo)
RequestedAssetCommitment = H(ASSETV1, requestedAssetHi, requestedAssetLo)
FeeCommitment            = H(FEEV1, ZEC, matcherFeeAmount, matcherFeeRecipientCommitment)
TradePartA               = H(TRDA_V1, OfferedAssetCommitment, offeredAmount,
                             RequestedAssetCommitment)
TradePartB               = H(TRDB_V1, requestedAmount, recipientCommitment, policyRoot)
TradeMeta                = H(TRDM_V1, FeeCommitment, nonce, expiry)
TradeCommitmentV1        = H(TRADE_V1, TradePartA, TradePartB, TradeMeta)
```

## AssetBase encoding

The canonical ZSA identifier is the 32-byte compressed Pallas encoding represented by `orchard::note::AssetBase`. Given bytes `b[0..31]`, interpret `b[0..15]` as an unsigned 128-bit little-endian `lo` limb and `b[16..31]` as an unsigned 128-bit little-endian `hi` limb. The circuit hashes `(hi, lo)`. Both limbs are range constrained, and re-encoding concatenates `LE128(lo) || LE128(hi)`, so the transformation is lossless.

## Orchard recipient encoding

Phase 0G used the canonical 43-byte raw Orchard payment address representation: an 11-byte diversifier followed by a 32-byte diversified transmission key. Split the raw byte sequence without reinterpretation into `bytes[0..15]`, `bytes[16..31]`, and `bytes[32..42]`, interpreted as unsigned little-endian limbs of 128, 128, and 88 bits. Then:

```text
SubjectCommitment  = H(SUBJECT1, subjectSecret)
ReceiverCommitment = H(RECEIVR1, receiverLimb0, receiverLimb1, receiverLimb2)
recipientCommitment = H(RCPBIND1, SubjectCommitment, ReceiverCommitment)
```

## Canonical-byte recipient commitments

`matcherFeeRecipientCommitment` is an input to `FeeCommitment` above, so its
derivation is part of the frozen contract. It commits to a raw canonical byte
string of any length by absorbing 16-byte little-endian chunks, binding both the
byte length and the chunk index so that no two distinct byte strings collide:

```text
state = H(domain, byteLength, limbCount)
state = H(state, limb[i], i)          for each 16-byte little-endian chunk
```

| Value | Domain | Committed bytes |
|---|---|---|
| `matcherFeeRecipientCommitment` | `FRCPTV1` | matcher fee receiver, 43 raw bytes |
| Phase 0F trade `recipientCommitment` | `RCPTV1` | Phase 0F trade recipient bytes |

Phase 0G replaced the `RCPTV1` derivation for the *trade* recipient with the
`RCPBIND1` binding described below. The matcher fee receiver still uses
`FRCPTV1` in both Phase 0F and Phase 0G, and both reference fixtures record the
same value.

## Nonce and expiry semantics

The nonce is an application-assigned unsigned 64-bit value included to distinguish otherwise identical intents. Uniqueness and single-use are matcher responsibilities. Expiry is unsigned Unix time in seconds. The matcher rejects an expired trade; the eligibility circuit additionally requires `credentialExpiry >= trade expiry`.

A trade is expired only once the current time is strictly greater than the committed expiry second, so the expiry second itself is still usable. The matcher applies that rule before verification, settlement construction, submission, and retry; see [architecture.md](architecture.md) §17 for which lifecycle transitions it gates.

## Privacy properties

Only `tradeCommitment` and the proof-specific root are public circuit signals. Asset identifiers, amounts, recipient encoding, fee amount and recipient commitment, nonce, expiry, credential subject secret, class, and jurisdiction are private witness values. This circuit-level statement does not by itself prove what every transaction field or external application log reveals.
