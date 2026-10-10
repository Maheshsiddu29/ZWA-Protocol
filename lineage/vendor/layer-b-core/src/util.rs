//! Frozen Layer-B verification utilities, extracted without RPC and block assembly.

use sha2::{Digest, Sha256};
use zcash_encoding::Vector;
use zcash_primitives::block::BlockHeader;
use zcash_primitives::transaction::sighash::{signature_hash, SignableInput};
use zcash_primitives::transaction::txid::TxIdDigester;
use zcash_primitives::transaction::{Authorization as TxAuth, Transaction, TransactionData};
use zcash_protocol::consensus::BranchId;
use zcash_protocol::value::Zatoshis;
use zcash_transparent::address::Script as TransparentScript;
use zcash_transparent::bundle::{Authorization as TransparentAuthz, Authorized as TransparentAuthorized};
use zcash_transparent::sighash::TransparentAuthorizingContext;

pub type H32 = [u8; 32];

pub fn blake2b_256(personal: &[u8; 16], data: &[u8]) -> H32 {
    blake2b_simd::Params::new()
        .hash_length(32)
        .personal(personal)
        .hash(data)
        .as_bytes()
        .try_into()
        .unwrap()
}

pub fn sha256(data: &[u8]) -> H32 {
    Sha256::digest(data).into()
}

pub fn sha256_hex(data: &[u8]) -> String {
    hex::encode(sha256(data))
}

pub fn h(b: &[u8]) -> String {
    hex::encode(b)
}

pub fn unhex32(s: &str) -> H32 {
    hex::decode(s.trim()).expect("hex").try_into().expect("32 bytes")
}

/// RPC display order (byte-reversed) to internal order.
pub fn from_display(s: &str) -> H32 {
    let mut b = unhex32(s);
    b.reverse();
    b
}

pub fn to_display(b: &H32) -> String {
    let mut r = *b;
    r.reverse();
    hex::encode(r)
}

/// Parsed block: header, its hash (internal order) and transactions.
pub struct ParsedBlock {
    pub header: BlockHeader,
    pub hash: H32,
    pub txs: Vec<Transaction>,
}

pub fn parse_block(bytes: &[u8]) -> Result<ParsedBlock, String> {
    let mut cursor = std::io::Cursor::new(bytes);
    let header = BlockHeader::read(&mut cursor).map_err(|e| format!("header: {e}"))?;
    let txs = Vector::read(&mut cursor, |r| Transaction::read(r, BranchId::Nu7)).map_err(|e| format!("txs: {e}"))?;
    if cursor.position() as usize != bytes.len() {
        return Err("trailing bytes after transactions".into());
    }
    let hash = header.hash().0;
    Ok(ParsedBlock { header, hash, txs })
}

/// Bitcoin-style transaction Merkle root over txids (the header's `merkle_root`).
pub fn merkle_root(hashes: impl Iterator<Item = [u8; 32]>) -> [u8; 32] {
    let mut hashes: Vec<[u8; 32]> = hashes.collect();
    while hashes.len() > 1 {
        hashes = hashes
            .chunks(2)
            .map(|c| match c {
                [a, b] => sha256d(a, b),
                [a] => sha256d(a, a),
                _ => unreachable!(),
            })
            .collect();
    }
    hashes[0]
}

fn sha256d(a: &[u8; 32], b: &[u8; 32]) -> [u8; 32] {
    let mut hasher = Sha256::new();
    hasher.update(a);
    hasher.update(b);
    Sha256::digest(hasher.finalize()).into()
}

// ---- Shielded sighash (H1B harness, unchanged) ------------------------------

pub fn shielded_sighash(tx: &Transaction) -> [u8; 32] {
    let data = tx.clone().into_data();
    let parts = data.digest(TxIdDigester);
    let mapped: TransactionData<SighashAuth> = data.map_authorization(MapTransparent, (), (), ());
    let hash = signature_hash(&mapped, &SignableInput::Shielded, &parts);
    *hash.as_ref()
}

#[derive(Debug)]
struct EmptyPrevouts;

impl TransparentAuthz for EmptyPrevouts {
    type ScriptSig = TransparentScript;
}

impl TransparentAuthorizingContext for EmptyPrevouts {
    fn input_amounts(&self) -> Vec<Zatoshis> {
        Vec::new()
    }

    fn input_scriptpubkeys(&self) -> Vec<TransparentScript> {
        Vec::new()
    }
}

struct MapTransparent;

impl zcash_transparent::bundle::MapAuth<TransparentAuthorized, EmptyPrevouts> for MapTransparent {
    fn map_script_sig(&self, script_sig: TransparentScript) -> TransparentScript {
        script_sig
    }

    fn map_authorization(&self, _auth: TransparentAuthorized) -> EmptyPrevouts {
        EmptyPrevouts
    }
}

struct SighashAuth;

impl TxAuth for SighashAuth {
    type TransparentAuth = EmptyPrevouts;
    type SaplingAuth = <zcash_primitives::transaction::Authorized as TxAuth>::SaplingAuth;
    type OrchardAuth = <zcash_primitives::transaction::Authorized as TxAuth>::OrchardAuth;
    type IssueAuth = orchard::issuance::Signed;
}
