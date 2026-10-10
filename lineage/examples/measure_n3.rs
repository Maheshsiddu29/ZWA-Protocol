//! Measures the frozen N = 3 fixture through the production path: verifier
//! load, request decode/canonicalization, per-step proof verification and
//! total Design-A time. Prints one JSON object. Use a release build:
//!
//! `cargo run --offline --locked --release --example measure_n3 -- [runs]`
//!
//! Fixture bytes are read from `tests/fixtures/`; nothing is generated.

use std::time::{Duration, Instant};

use serde_json::{json, Value};
use zwa_lineage::{
    decode_request, encode_request, ChainRef, DesignAResult, FrozenStepVerifier,
    Phase1LineageService, RawLineageEvidence, RawStep, RootedLineageEvidence,
};

type Error = Box<dyn std::error::Error>;

fn unhex(v: &Value) -> Result<Vec<u8>, Error> {
    Ok(hex::decode(v.as_str().ok_or("not a string")?)?)
}

fn arr32(v: &Value) -> Result<[u8; 32], Error> {
    unhex(v)?.try_into().map_err(|_| "not 32 bytes".into())
}

fn field<'a>(v: &'a Value, key: &str) -> Result<&'a Value, Error> {
    v.get(key).ok_or_else(|| format!("missing {key}").into())
}

fn micros(d: Duration) -> u128 {
    d.as_micros()
}

fn main() -> Result<(), Error> {
    let runs: usize = std::env::args().nth(1).map_or(Ok(5), |s| s.parse())?;
    let dir = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures");
    let manifest: Value =
        serde_json::from_str(&std::fs::read_to_string(dir.join("fixtures.json"))?)?;
    let n3 = field(field(&manifest, "lineages")?, "n3")?;
    let cr = field(n3, "chain_ref")?;
    let chain_ref = ChainRef::new(
        arr32(field(cr, "network_id")?)?,
        u32::try_from(field(cr, "height")?.as_u64().ok_or("height")?)?,
        arr32(field(cr, "block_hash_internal")?)?,
        zwa_lineage::CanonicalOrchardRoot::from_slice(&unhex(field(cr, "orchard_root")?)?)
            .map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?
    .encode();
    let mut parts = Vec::new();
    for name in field(n3, "steps")?.as_array().ok_or("steps")? {
        let name = name.as_str().ok_or("step name")?;
        let s = field(field(&manifest, "steps")?, name)?;
        parts.push((
            unhex(field(s, "cmx_old")?)?,
            unhex(field(s, "nf_old")?)?,
            unhex(field(s, "cmx_new")?)?,
            std::fs::read(dir.join("step-proofs").join(format!("{name}.bin")))?,
        ));
    }
    let root = unhex(field(n3, "root_cmx")?)?;
    let terminal = unhex(field(n3, "terminal_cmx")?)?;
    let raw = RawLineageEvidence {
        chain_ref: Some(&chain_ref),
        root_cmx: &root,
        terminal_cmx: &terminal,
        steps: parts
            .iter()
            .map(|(a, n, b, p)| RawStep {
                cmx_old: a,
                nf_old: n,
                cmx_new: b,
                proof: p,
            })
            .collect(),
    };
    let evidence = RootedLineageEvidence::parse(&raw).map_err(|e| e.to_string())?;
    let request = encode_request(&evidence).map_err(|e| e.to_string())?;

    let t = Instant::now();
    let verifier = FrozenStepVerifier::load()?;
    let load = t.elapsed();
    let service = Phase1LineageService::phase1(verifier);

    let mut samples = Vec::new();
    for _ in 0..runs {
        let t = Instant::now();
        let decoded = decode_request(&request).map_err(|e| e.to_string())?;
        let parse = t.elapsed();
        let DesignAResult::LineageProofValid(report) = service.verify_design_a(&decoded.evidence)
        else {
            return Err("N = 3 fixture did not verify".into());
        };
        samples.push(json!({
            "decode_and_canonicalize_us": micros(parse),
            "step_verify_us": report.step_verify_times.iter().map(|d| micros(*d)).collect::<Vec<_>>(),
            "design_a_total_us": micros(report.total_time),
        }));
    }
    let out = json!({
        "fixture": "n3 (freeze/FROZEN_ROOTED_FIXTURE.md)",
        "request_bytes": request.len(),
        "verifier_load_us": micros(load),
        "runs": runs,
        "samples": samples,
    });
    println!("{}", serde_json::to_string_pretty(&out)?);
    Ok(())
}
