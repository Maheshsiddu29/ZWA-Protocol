//! `chain_ref` version 1 and `network_id` (`CHAINREF_SPEC.md` §2–§3).

use ff::PrimeField;
use pasta_curves::pallas;

use crate::util::{blake2b_256, H32};

pub const CHAINREF_TAG: &[u8; 16] = b"ZWA-LB-CHAINREF:";
pub const CHAINREF_VERSION: u8 = 1;
pub const CHAINREF_LEN: usize = 117;

/// sha256 of the pinned `circuit_description_zsa` (Action closure CD-02).
pub const ACTION_VK_ID: &str = "8900633438bce0f838309bddb817b4ea0b3b705a8d778696246c0428816faa0e";
/// Research rule-set tag. Production value: OPEN.
pub const RULESET_TAG: &str = "zebra=8c9c93fd;orchard=cf801a5;librustzcash=c5c232d;features=tx_v6,nu7";

/// Canonical genesis hashes (display order), Zebra `8c9c93fd`.
pub const GENESIS_MAINNET: &str = "00040fe8ec8471911baa1db1266ea15dd06b4a8a5c453883c000b031973dce08";
pub const GENESIS_TESTNET: &str = "05a60a92d99d85997cce3b87616c089f6124d7342af37106edc76126334a2c38";
pub const GENESIS_REGTEST: &str = "029f11d80ef9765602235e1bc9727e3eb6ba20839319f761fee920d63401e327";

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum NetworkKind {
    Mainnet = 0,
    Testnet = 1,
    Regtest = 2,
}

impl NetworkKind {
    pub fn genesis_display(self) -> &'static str {
        match self {
            NetworkKind::Mainnet => GENESIS_MAINNET,
            NetworkKind::Testnet => GENESIS_TESTNET,
            NetworkKind::Regtest => GENESIS_REGTEST,
        }
    }
    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "mainnet" => Some(NetworkKind::Mainnet),
            "testnet" => Some(NetworkKind::Testnet),
            "regtest" => Some(NetworkKind::Regtest),
            _ => None,
        }
    }
}

/// The inputs of `network_id`. The verifier builds this from its own node and
/// configuration, never from prover data.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct NetworkParams {
    pub kind: NetworkKind,
    /// Internal byte order.
    pub genesis_hash: H32,
    /// (branch_id, activation_height), any order; sorted when hashed.
    pub upgrades: Vec<(u32, u32)>,
    pub action_vk_id: H32,
    pub ruleset_tag: String,
}

impl NetworkParams {
    pub fn network_id(&self) -> H32 {
        let mut ups = self.upgrades.clone();
        ups.sort_by_key(|(id, height)| (*height, *id));
        let mut m = vec![self.kind as u8];
        m.extend_from_slice(&self.genesis_hash);
        m.push(ups.len() as u8);
        for (id, height) in ups {
            m.extend_from_slice(&id.to_le_bytes());
            m.extend_from_slice(&height.to_le_bytes());
        }
        m.extend_from_slice(&self.action_vk_id);
        m.push(self.ruleset_tag.len() as u8);
        m.extend_from_slice(self.ruleset_tag.as_bytes());
        blake2b_256(b"ZWA_LB_NetworkID", &m)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct ChainRef {
    pub network_id: H32,
    pub height: u32,
    /// Internal byte order.
    pub block_hash: H32,
    /// Canonical LE `F_{q_P}` bytes.
    pub orchard_root: H32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ChainRefDecodeError {
    Malformed,
    UnsupportedVersion,
}

impl ChainRef {
    pub fn encode(&self) -> Vec<u8> {
        let mut v = Vec::with_capacity(CHAINREF_LEN);
        v.extend_from_slice(CHAINREF_TAG);
        v.push(CHAINREF_VERSION);
        v.extend_from_slice(&self.network_id);
        v.extend_from_slice(&self.height.to_le_bytes());
        v.extend_from_slice(&self.block_hash);
        v.extend_from_slice(&self.orchard_root);
        debug_assert_eq!(v.len(), CHAINREF_LEN);
        v
    }

    /// Strict decoding: exact length, exact tag, version 1, height ≥ 1,
    /// canonical root.
    pub fn decode(b: &[u8]) -> Result<Self, ChainRefDecodeError> {
        if b.len() != CHAINREF_LEN || &b[..16] != CHAINREF_TAG {
            return Err(ChainRefDecodeError::Malformed);
        }
        if b[16] != CHAINREF_VERSION {
            return Err(ChainRefDecodeError::UnsupportedVersion);
        }
        let network_id: H32 = b[17..49].try_into().unwrap();
        let height = u32::from_le_bytes(b[49..53].try_into().unwrap());
        let block_hash: H32 = b[53..85].try_into().unwrap();
        let orchard_root: H32 = b[85..117].try_into().unwrap();
        if height == 0 || bool::from(pallas::Base::from_repr(orchard_root).is_none()) {
            return Err(ChainRefDecodeError::Malformed);
        }
        Ok(ChainRef { network_id, height, block_hash, orchard_root })
    }

    pub fn id(&self) -> H32 {
        blake2b_256(b"ZWA_LB_ChainRefH", &self.encode())
    }
}
