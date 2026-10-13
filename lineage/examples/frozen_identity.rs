//! Prints the identity of the frozen verifier configuration this build
//! derives: VK* identity and parameter digest, recomputed from the frozen
//! R2B-L1 `params()` and `keygen_vk()`, compared with the pinned constants.
//!
//! `cargo run --offline --locked --release --example frozen_identity`

use std::time::Instant;

use zwa_lineage::frozen::{params_identity, vk_identity, FROZEN_PARAMS_SHA256, FROZEN_VK_ID};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let t = Instant::now();
    let params = zwa_lineage_r2b_l1::prover::params();
    let params_time = t.elapsed();
    let t = Instant::now();
    let vk = zwa_lineage_r2b_l1::prover::keygen_vk(&params);
    let vk_time = t.elapsed();
    let vk_id = vk_identity(&vk);
    let params_sha256 = params_identity(&params)?;
    println!("vk_id          {}", hex::encode(vk_id));
    println!(
        "vk_id pinned   {} {}",
        hex::encode(FROZEN_VK_ID),
        vk_id == FROZEN_VK_ID
    );
    println!("params_sha256  {}", hex::encode(params_sha256));
    println!(
        "params pinned  {} {}",
        hex::encode(FROZEN_PARAMS_SHA256),
        params_sha256 == FROZEN_PARAMS_SHA256
    );
    println!("params_us      {}", params_time.as_micros());
    println!("keygen_vk_us   {}", vk_time.as_micros());
    Ok(())
}
