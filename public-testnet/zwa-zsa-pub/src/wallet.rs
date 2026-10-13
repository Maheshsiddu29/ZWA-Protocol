//! Disposable wallets on the community ZSA crate graph (via Zkool's `rlz`).
//!
//! Each role has its own SQLite wallet under the owner-only root. Seeds are
//! generated inside `rlz` from 32 bytes of OS randomness and never leave the
//! wallet file; this module never reads, prints or exports them.

use std::fs;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};
use rlz::api::account::{get_account_ufvk, get_addresses, new_account, NewAccount};
use rlz::api::coin::Coin;
use rlz::api::network::get_current_height;
use rlz::api::pay::{extract_transaction, prepare, sign_transaction, PaymentOptions};
use rlz::pay::pool::{ALL_POOLS, POOL_ORCHARD, POOL_TRANSPARENT};
use rlz::pay::Recipient;
use serde::{Deserialize, Serialize};
use zcash_address::unified::{self, Container, Encoding};

use crate::policy::{Profile, ProfileKind};

/// Zkool's coin index for the community ZSA Testnet (`ZsaRegtest`, branch
/// `Zsa` = 0x77190ad8, regtest address encoding).
const ZSA_COIN: u8 = 3;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, clap::ValueEnum)]
#[serde(rename_all = "lowercase")]
pub enum Role {
    Issuer,
    Recipient,
    /// Lab-only stand-in for the public faucet, funded by local coinbase.
    Labfaucet,
}

impl Role {
    pub fn name(self) -> &'static str {
        match self {
            Role::Issuer => "issuer",
            Role::Recipient => "recipient",
            Role::Labfaucet => "labfaucet",
        }
    }
}

#[derive(Serialize, Deserialize)]
struct AccountFile {
    account: u32,
    birth: u32,
    role: Role,
}

pub struct Wallet {
    pub coin: Coin,
    pub account: u32,
    pub role: Role,
}

fn role_dir(root: &Path, role: Role) -> PathBuf {
    root.join(role.name())
}

/// Write a new owner-only file; never overwrites.
pub fn write_private(path: &Path, bytes: &[u8]) -> Result<()> {
    use std::io::Write;
    let mut f = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(path)
        .with_context(|| format!("create {}", path.display()))?;
    f.write_all(bytes)?;
    f.sync_all()?;
    Ok(())
}

fn tighten(dir: &Path) -> Result<()> {
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        if entry.file_type()?.is_file() {
            fs::set_permissions(entry.path(), fs::Permissions::from_mode(0o600))?;
        }
    }
    Ok(())
}

async fn open_coin(dir: &Path, profile: &Profile) -> Result<Coin> {
    let db = dir.join("wallet.db");
    let coin = Coin::new(Some(ZSA_COIN))
        .open_database(db.to_str().context("db path")?.to_string(), None)
        .await
        .context("open wallet database")?
        .set_lwd(0, profile.lwd_url.clone())?;
    tighten(dir)?;
    Ok(coin)
}

pub async fn init(root: &Path, profile: &Profile, role: Role) -> Result<serde_json::Value> {
    if role == Role::Labfaucet && profile.kind != ProfileKind::Lab {
        bail!("the lab faucet role exists only in a lab root");
    }
    let dir = role_dir(root, role);
    if fs::symlink_metadata(&dir).is_ok() {
        bail!("{} wallet already exists; wallets are never reused or overwritten", role.name());
    }
    fs::create_dir(&dir)?;
    fs::set_permissions(&dir, fs::Permissions::from_mode(0o700))?;
    let coin = open_coin(&dir, profile).await?;
    let birth = get_current_height(&coin).await? ;
    let na = NewAccount {
        icon: None,
        name: format!("zwa-{}", role.name()),
        restore: false,
        key: String::new(), // rlz generates a fresh 24-word seed internally
        passphrase: Some(String::new()),
        fingerprint: None,
        aindex: 0,
        birth: Some(birth),
        folder: String::new(),
        pools: Some(ALL_POOLS),
        use_internal: false,
        internal: false,
        hw: 0,
    };
    let account = new_account(&na, &coin).await.context("create account")?;
    let meta = AccountFile { account, birth, role };
    write_private(&dir.join("account.json"), &serde_json::to_vec_pretty(&meta)?)?;
    tighten(&dir)?;
    Ok(serde_json::json!({ "role": role.name(), "birthHeight": birth, "created": true }))
}

pub async fn open(root: &Path, profile: &Profile, role: Role) -> Result<Wallet> {
    let dir = role_dir(root, role);
    let meta: AccountFile = serde_json::from_slice(
        &fs::read(dir.join("account.json")).with_context(|| format!("{} wallet missing", role.name()))?,
    )?;
    if meta.role != role {
        bail!("wallet role file mismatch");
    }
    let coin = open_coin(&dir, profile).await?.set_account(meta.account).await?;
    Ok(Wallet { coin, account: meta.account, role })
}

impl Wallet {
    pub async fn sync(&self) -> Result<u32> {
        let height = get_current_height(&self.coin).await?;
        rlz::sync::synchronize_impl((), vec![self.account], height, 10_000, 100, 10_000, false, &self.coin)
            .await
            .context("wallet sync")?;
        Ok(height)
    }

    pub async fn height(&self) -> Result<u32> {
        get_current_height(&self.coin).await
    }

    /// Orchard-only unified address (the faucet and transfers target this).
    pub async fn orchard_address(&self) -> Result<String> {
        get_addresses(POOL_ORCHARD, &self.coin)
            .await?
            .ua
            .context("no Orchard unified address")
    }

    pub async fn transparent_address(&self) -> Result<String> {
        get_addresses(ALL_POOLS, &self.coin).await?.taddr.context("no transparent address")
    }

    /// The Orchard full viewing key, used only in-process to confirm that a
    /// prepared transfer pays this wallet. It is never written or printed.
    pub async fn orchard_fvk(&self) -> Result<orchard::keys::FullViewingKey> {
        let ufvk = get_account_ufvk(self.account, POOL_ORCHARD, &self.coin).await?;
        let (_, _, ufvk) = unified::Ufvk::decode(&ufvk).map_err(|e| anyhow::anyhow!("ufvk: {e}"))?;
        for item in ufvk.items() {
            if let unified::Fvk::Orchard(bytes) = item {
                return orchard::keys::FullViewingKey::from_bytes(&bytes).context("orchard fvk");
            }
        }
        bail!("UFVK has no Orchard component")
    }

    /// ZEC balance per pool [T, S, O, IW] and ZSA holdings (private).
    pub async fn balances(&self) -> Result<serde_json::Value> {
        let zec = rlz::api::sync::balance(&self.coin).await?;
        let holdings = rlz::api::zsa::list_zsa_holdings(&self.coin).await?;
        let assets: Vec<_> = holdings
            .iter()
            .map(|h| {
                serde_json::json!({
                    "assetBase": hex::encode(&h.asset_base),
                    "assetDescHash": hex::encode(&h.asset_desc_hash),
                    "balance": h.balance,
                    "finalized": h.finalized,
                })
            })
            .collect();
        Ok(serde_json::json!({
            "role": self.role.name(),
            "zecZat": { "transparent": zec.0[0], "sapling": zec.0[1], "orchard": zec.0[2] },
            "assets": assets,
        }))
    }

    pub async fn zsa_balance(&self, asset_base: &[u8; 32]) -> Result<u64> {
        let holdings = rlz::api::zsa::list_zsa_holdings(&self.coin).await?;
        Ok(holdings.iter().filter(|h| h.asset_base == asset_base).map(|h| h.balance).sum())
    }

    pub async fn orchard_zec(&self) -> Result<u64> {
        Ok(rlz::api::sync::balance(&self.coin).await?.0[2])
    }

    /// Build and sign a finalized first issuance. Nothing is sent.
    pub async fn build_issuance(&self, name: &str, amount: u64) -> Result<Vec<u8>> {
        rlz::api::issuance::issue_asset(name.to_string(), amount, true, true, None, self.account, &self.coin)
            .await
            .context("issuance construction")
    }

    /// Build and sign a ZSA (or ZEC, when `asset_base` is empty) payment to a
    /// shielded address. Nothing is sent.
    pub async fn build_payment(&self, address: String, amount: u64, asset_base: Vec<u8>) -> Result<Vec<u8>> {
        let recipient = Recipient {
            address,
            amount,
            pools: None,
            user_memo: None,
            memo_bytes: None,
            price: None,
            asset_base,
            asset_name: None,
        };
        let options = PaymentOptions {
            src_pools: ALL_POOLS,
            recipient_pays_fee: false,
            smart_transparent: false,
            category: None,
        };
        let pczt = prepare(&[recipient], options, &self.coin).await.context("plan payment")?;
        let signed = sign_transaction(&pczt, &self.coin).await.context("sign payment")?;
        extract_transaction(&signed).await.context("extract payment")
    }

    /// LAB ONLY: shield this wallet's entire transparent balance to its own
    /// Orchard address (recipient pays the fee, so there is no transparent
    /// change). Coinbase spends may not have transparent outputs.
    pub async fn build_shield_all(&self) -> Result<Vec<u8>> {
        let recipient = Recipient {
            address: self.orchard_address().await?,
            amount: 0, // replaced by rlz with the full transparent balance
            pools: None,
            user_memo: None,
            memo_bytes: None,
            price: None,
            asset_base: vec![],
            asset_name: None,
        };
        let options = PaymentOptions {
            src_pools: POOL_TRANSPARENT,
            recipient_pays_fee: true,
            smart_transparent: true,
            category: None,
        };
        let pczt = prepare(&[recipient], options, &self.coin).await.context("plan shielding")?;
        let signed = sign_transaction(&pczt, &self.coin).await.context("sign shielding")?;
        extract_transaction(&signed).await.context("extract shielding")
    }

    /// Submit through lightwalletd. Callers must pass the broadcast gate first.
    pub async fn submit(&self, height: u32, bytes: &[u8]) -> Result<String> {
        rlz::api::pay::broadcast_transaction(height, bytes, &self.coin)
            .await
            .context("lightwalletd SendTransaction")
    }
}
