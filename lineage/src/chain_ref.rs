//! `chain_ref` version 1: the Layer-B chain context (contract §C9, §G5).
//!
//! Ported from `post-h1f/layer-b/experiments/src/chainref.rs` (research
//! `CHAINREF_SPEC.md` §2) without changing the encoding:
//!
//! ```text
//! "ZWA-LB-CHAINREF:" ‖ 0x01 ‖ network_id(32) ‖ height(u32 LE)
//!                    ‖ block_hash(32, internal order) ‖ orchard_root(32, canonical LE)
//! ```
//!
//! A `chain_ref` is prover-supplied and never trusted. Decoding it only
//! establishes that it is well formed. Whether the named block is on the
//! verifier's own selected chain, whether its tree state matches and whether it
//! is final is decided by a [`crate::auth::ChainContextAuthenticator`]. It has
//! no meaning inside the lineage circuit.

use core::fmt;

use crate::field::CanonicalOrchardRoot;

/// Domain tag.
pub const CHAINREF_TAG: &[u8; 16] = b"ZWA-LB-CHAINREF:";
/// The only supported version.
pub const CHAINREF_VERSION: u8 = 1;
/// Exact encoded length.
pub const CHAINREF_LEN: usize = 117;
/// BLAKE2b personalization of [`ChainRef::id`].
pub const CHAINREF_ID_PERSONAL: &[u8; 16] = b"ZWA_LB_ChainRefH";

/// Why `chain_ref` bytes were rejected.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChainRefError {
    /// Wrong length, wrong tag, height 0 or a non-canonical Orchard root.
    Malformed,
    /// Version byte other than 1.
    UnsupportedVersion,
}

impl fmt::Display for ChainRefError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Malformed => f.write_str("malformed chain_ref"),
            Self::UnsupportedVersion => f.write_str("unsupported chain_ref version"),
        }
    }
}

/// A strictly decoded `chain_ref`.
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
pub struct ChainRef {
    network_id: [u8; 32],
    height: u32,
    block_hash: [u8; 32],
    orchard_root: CanonicalOrchardRoot,
}

impl ChainRef {
    /// Builds a reference from its fields.
    ///
    /// # Errors
    ///
    /// [`ChainRefError::Malformed`] for height 0 (genesis carries no Orchard
    /// state).
    pub fn new(
        network_id: [u8; 32],
        height: u32,
        block_hash: [u8; 32],
        orchard_root: CanonicalOrchardRoot,
    ) -> Result<Self, ChainRefError> {
        if height == 0 {
            return Err(ChainRefError::Malformed);
        }
        Ok(Self {
            network_id,
            height,
            block_hash,
            orchard_root,
        })
    }

    /// Strict decoding: exact length, exact tag, version 1, height ≥ 1,
    /// canonical root. The checks run in the research order, so a wrong tag
    /// with a wrong version is `Malformed`.
    ///
    /// # Errors
    ///
    /// See [`ChainRefError`].
    pub fn decode(bytes: &[u8]) -> Result<Self, ChainRefError> {
        let b: &[u8; CHAINREF_LEN] = bytes.try_into().map_err(|_| ChainRefError::Malformed)?;
        let (tag, rest) = b.split_at(16);
        if tag != CHAINREF_TAG {
            return Err(ChainRefError::Malformed);
        }
        let (version, rest) = rest.split_at(1);
        if version != [CHAINREF_VERSION] {
            return Err(ChainRefError::UnsupportedVersion);
        }
        let (network_id, rest) = rest.split_at(32);
        let (height, rest) = rest.split_at(4);
        let (block_hash, orchard_root) = rest.split_at(32);
        let network_id: [u8; 32] = network_id
            .try_into()
            .map_err(|_| ChainRefError::Malformed)?;
        let height: [u8; 4] = height.try_into().map_err(|_| ChainRefError::Malformed)?;
        let block_hash: [u8; 32] = block_hash
            .try_into()
            .map_err(|_| ChainRefError::Malformed)?;
        let orchard_root =
            CanonicalOrchardRoot::from_slice(orchard_root).map_err(|_| ChainRefError::Malformed)?;
        Self::new(
            network_id,
            u32::from_le_bytes(height),
            block_hash,
            orchard_root,
        )
    }

    /// The canonical 117-byte encoding.
    #[must_use]
    pub fn encode(&self) -> [u8; CHAINREF_LEN] {
        let mut v = [0u8; CHAINREF_LEN];
        let parts: [&[u8]; 6] = [
            CHAINREF_TAG,
            &[CHAINREF_VERSION],
            &self.network_id,
            &self.height.to_le_bytes(),
            &self.block_hash,
            self.orchard_root.as_bytes(),
        ];
        let mut at = 0;
        for part in parts {
            for (dst, src) in v.iter_mut().skip(at).zip(part) {
                *dst = *src;
            }
            at += part.len();
        }
        v
    }

    /// `chain_ref.id = BLAKE2b-256("ZWA_LB_ChainRefH", encoding)`: a log and
    /// tracker key, not a security-bearing field.
    #[must_use]
    pub fn id(&self) -> [u8; 32] {
        crate::blake2b_256(CHAINREF_ID_PERSONAL, &self.encode())
    }

    /// Network identity the prover claims.
    #[must_use]
    pub const fn network_id(&self) -> &[u8; 32] {
        &self.network_id
    }

    /// Block height (≥ 1).
    #[must_use]
    pub const fn height(&self) -> u32 {
        self.height
    }

    /// Block hash, internal byte order.
    #[must_use]
    pub const fn block_hash(&self) -> &[u8; 32] {
        &self.block_hash
    }

    /// Orchard note-commitment tree root after the block.
    #[must_use]
    pub const fn orchard_root(&self) -> &CanonicalOrchardRoot {
        &self.orchard_root
    }
}

impl fmt::Debug for ChainRef {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ChainRef")
            .field("network_id", &hex::encode(self.network_id))
            .field("height", &self.height)
            .field("block_hash", &hex::encode(self.block_hash))
            .field("orchard_root", &self.orchard_root)
            .finish()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> ChainRef {
        let root = CanonicalOrchardRoot::from_array([7u8; 32]).unwrap();
        ChainRef::new([1u8; 32], 4, [2u8; 32], root).unwrap()
    }

    #[test]
    fn round_trips() {
        let r = sample();
        let enc = r.encode();
        assert_eq!(&enc[..16], CHAINREF_TAG);
        assert_eq!(ChainRef::decode(&enc), Ok(r));
    }

    // Research vectors CR-05 … CR-09 (layer-b matrix, expected outcomes).
    #[test]
    fn research_decode_vectors() {
        let enc = sample().encode();
        let mut v = enc;
        v[16] = 2;
        assert_eq!(ChainRef::decode(&v), Err(ChainRefError::UnsupportedVersion)); // CR-06
        let mut v = enc;
        v[0] ^= 1;
        assert_eq!(ChainRef::decode(&v), Err(ChainRefError::Malformed)); // CR-07
        assert_eq!(ChainRef::decode(&enc[..116]), Err(ChainRefError::Malformed)); // CR-08a
        let mut long = enc.to_vec();
        long.push(0);
        assert_eq!(ChainRef::decode(&long), Err(ChainRefError::Malformed)); // CR-08b
        let mut v = enc;
        v[49..53].copy_from_slice(&0u32.to_le_bytes());
        assert_eq!(ChainRef::decode(&v), Err(ChainRefError::Malformed)); // CR-09
        let mut v = enc;
        v[85..117].copy_from_slice(&[0xff; 32]);
        assert_eq!(ChainRef::decode(&v), Err(ChainRefError::Malformed)); // CR-05 shape
    }
}
