//! Read-only lightwalletd queries used for identity checks and inclusion
//! verification. Submission lives in `wallet::broadcast`, behind the gate.

use anyhow::{bail, Context, Result};
use rlz::lwd::compact_tx_streamer_client::CompactTxStreamerClient;
use rlz::lwd::{BlockId, ChainSpec, Empty, TxFilter};
use serde::Serialize;
use sha2::{Digest, Sha256};
use tonic::transport::{Channel, ClientTlsConfig, Endpoint};

use crate::policy::{ObservedIdentity, Profile, ProfileKind};

pub type Lwd = CompactTxStreamerClient<Channel>;

pub async fn connect(profile: &Profile) -> Result<Lwd> {
    let mut endpoint = Endpoint::from_shared(profile.lwd_url.clone())?
        .connect_timeout(std::time::Duration::from_secs(20))
        .timeout(std::time::Duration::from_secs(60));
    if profile.kind == ProfileKind::Public {
        endpoint = endpoint.tls_config(ClientTlsConfig::new().with_webpki_roots())?;
    }
    let channel = endpoint.connect().await.context("lightwalletd connection")?;
    Ok(CompactTxStreamerClient::new(channel).max_decoding_message_size(32 << 20))
}

/// lightwalletd carries block hashes in internal byte order.
fn display_hash(internal: &[u8]) -> Result<String> {
    if internal.len() != 32 {
        bail!("block hash must be 32 bytes");
    }
    let mut v = internal.to_vec();
    v.reverse();
    Ok(hex::encode(v))
}

pub async fn identity(lwd: &mut Lwd) -> Result<ObservedIdentity> {
    let info = lwd.get_lightd_info(Empty {}).await?.into_inner();
    let block1 = lwd
        .get_block(BlockId { height: 1, hash: vec![] })
        .await
        .context("block 1")?
        .into_inner();
    let tip = lwd.get_latest_block(ChainSpec {}).await?.into_inner();
    Ok(ObservedIdentity {
        chain_name: info.chain_name,
        branch_id_hex: info.consensus_branch_id,
        node_subversion: info.zcashd_subversion,
        tip_height: tip.height,
        genesis_hash: display_hash(&block1.prev_hash)?,
        block1_hash: display_hash(&block1.hash)?,
    })
}

pub async fn tip(lwd: &mut Lwd) -> Result<(u64, String)> {
    let tip = lwd.get_latest_block(ChainSpec {}).await?.into_inner();
    Ok((tip.height, display_hash(&tip.hash)?))
}

/// Public inclusion evidence for one transaction.
#[derive(Clone, Debug, Serialize)]
pub struct Inclusion {
    pub txid: String,
    pub tx_sha256: String,
    pub mined: bool,
    pub block_height: Option<u64>,
    pub block_hash: Option<String>,
    pub block_time: Option<u32>,
    pub tx_index_in_block: Option<u64>,
    pub observed_tip_height: u64,
    pub observed_tip_hash: String,
    pub confirmations: u64,
}

fn txid_internal(txid_display: &str) -> Result<Vec<u8>> {
    let mut bytes = hex::decode(txid_display).context("txid hex")?;
    if bytes.len() != 32 {
        bail!("txid must be 32 bytes");
    }
    bytes.reverse();
    Ok(bytes)
}

/// Fetch the transaction by its canonical (node) txid, require the returned
/// bytes to be identical to the prepared bytes, then prove membership by
/// finding the txid in the compact block at the reported height.
pub async fn inclusion(lwd: &mut Lwd, txid_display: &str, expected_sha256: &str) -> Result<Inclusion> {
    let internal = txid_internal(txid_display)?;
    let raw = lwd
        .get_transaction(TxFilter { block: None, index: 0, hash: internal.clone() })
        .await
        .context("GetTransaction")?
        .into_inner();
    let tx_sha256 = hex::encode(Sha256::digest(&raw.data));
    if tx_sha256 != expected_sha256 {
        bail!("node returned different bytes for this txid");
    }
    let (tip_height, tip_hash) = tip(lwd).await?;
    // lightwalletd reports 0 for mempool and u64::MAX-style sentinels for
    // transactions outside the best chain; only a plausible height counts.
    if raw.height == 0 || raw.height > tip_height {
        return Ok(Inclusion {
            txid: txid_display.to_string(),
            tx_sha256,
            mined: false,
            block_height: None,
            block_hash: None,
            block_time: None,
            tx_index_in_block: None,
            observed_tip_height: tip_height,
            observed_tip_hash: tip_hash,
            confirmations: 0,
        });
    }
    let block = lwd
        .get_block(BlockId { height: raw.height, hash: vec![] })
        .await?
        .into_inner();
    let ctx = block
        .vtx
        .iter()
        .find(|c| c.hash == internal)
        .context("transaction is not in the compact block at its reported height")?;
    Ok(Inclusion {
        txid: txid_display.to_string(),
        tx_sha256,
        mined: true,
        block_height: Some(block.height),
        block_hash: Some(display_hash(&block.hash)?),
        block_time: Some(block.time),
        tx_index_in_block: Some(ctx.index),
        observed_tip_height: tip_height,
        observed_tip_hash: tip_hash,
        confirmations: tip_height - block.height + 1,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hashes_flip_byte_order() {
        let mut internal = vec![0u8; 32];
        internal[0] = 0xb7;
        internal[31] = 0x03;
        let shown = display_hash(&internal).unwrap();
        assert!(shown.starts_with("03") && shown.ends_with("b7"));
        assert_eq!(txid_internal(&shown).unwrap(), internal);
        assert!(display_hash(&[0u8; 31]).is_err());
        assert!(txid_internal("zz").is_err());
    }
}
