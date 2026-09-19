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

| Name | Integer |
|---|---:|
| `ASSETV1` | 18387490596738609 |
| `ISSMETA1` | 5283658379176460593 |
| `ISSUEV1` | 20639290677876273 |
| `FEEV1` | 301809882673 |
| `TRDA_V1` | 23734351151715889 |
| `TRDB_V1` | 23734351168493105 |
| `TRDM_V1` | 23734351353042481 |
| `TRADE_V1` | 6075990608753677873 |
| `ZEC` | 5915971 |
| `SUBJECT1` | 6004778564925477937 |
| `RECEIVR1` | 5928218449365324337 |
| `RCPBIND1` | 5927669780177634353 |
| `CREDMETA` | 4851015908287927361 |
| `CRED_V1` | 18949280892933681 |
| `ELIGPOL1` | 4993446657485917233 |

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

## Nonce and expiry semantics

The nonce is an application-assigned unsigned 64-bit value included to distinguish otherwise identical intents. Uniqueness and single-use are matcher responsibilities. Expiry is unsigned Unix time in seconds. The matcher rejects an expired trade; the eligibility circuit additionally requires `credentialExpiry >= trade expiry`.

## Privacy properties

Only `tradeCommitment` and the proof-specific root are public circuit signals. Asset identifiers, amounts, recipient encoding, fee amount and recipient commitment, nonce, expiry, credential subject secret, class, and jurisdiction are private witness values. This circuit-level statement does not by itself prove what every transaction field or external application log reveals.
