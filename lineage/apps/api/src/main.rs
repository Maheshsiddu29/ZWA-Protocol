use std::{
    env,
    net::SocketAddr,
    path::Path,
    sync::Arc,
    time::{Duration, Instant},
};

use axum::{
    body::Bytes,
    extract::{rejection::BytesRejection, DefaultBodyLimit, State},
    http::{header::CONTENT_TYPE, HeaderMap, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Router,
};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tokio::sync::Semaphore;
use zwa_lineage::{
    frozen::VerifierIdentity,
    phase2::{
        Phase2LineageVerifier, Phase2VerifierConfig, SnapshotChainView, SnapshotId, TradePermission,
    },
    LineageResult, MAX_STEPS, STEP_PROOF_LEN,
};

const POLICY: &[u8] = include_bytes!("../../../tests/fixtures/phase2/policy.bin");
const N3: &[u8] = include_bytes!("../../../tests/fixtures/phase2/n3-v3.bin");
const CKN: &[u8] = include_bytes!("../../../tests/fixtures/phase2/ckn-v3.bin");
const POLICY_DIGEST: &str = "cf6516c1d87c5bf5a1add3ec93cedb0638c97b23755a5a6b6cebe600449f22f2";
const POLICY_SHA: &str = "ce433a74be5510050d7f7f85cc1360f28c9e6bf71acbd68a1e7862a0925d14be";
const N3_SHA: &str = "0821a7abafca556bdcf9403ed9e040603194bf5d46650316d42554ee9c96ebb0";
const CKN_SHA: &str = "f1edfd8a4cae7a1efff33290dd3dded70b8ee6eb33382bc71a64dafc7910f5ef";
// v3 fixed prefix + root + terminal + u16 count + 1024 * (96 public + u16 length + 5120 proof).
const MAX_EVIDENCE: usize =
    16 + 1 + 32 + 117 + 32 + 32 + 1169 + 1028 + 2 + MAX_STEPS * (96 + 2 + STEP_PROOF_LEN);
const DEFAULT_MAX_BODY: usize = 7_200_000;

#[derive(Clone)]
struct AppState {
    production: Arc<Phase2LineageVerifier>,
    a7: Option<Arc<Phase2LineageVerifier>>,
    a4: Option<Arc<Phase2LineageVerifier>>,
    ckn: Option<Arc<Phase2LineageVerifier>>,
    reorg: Option<Arc<Phase2LineageVerifier>>,
    view: SnapshotId,
    limit: Arc<Semaphore>,
    max_body: usize,
    log_level: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct VerifyRequest {
    evidence_encoding: String,
    evidence: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct DemoRequest {
    scenario_id: String,
}

struct ApiJson(Value);
impl IntoResponse for ApiJson {
    fn into_response(self) -> Response {
        ([(CONTENT_TYPE, "application/json")], self.0.to_string()).into_response()
    }
}
fn hash(bytes: &[u8]) -> String {
    hex::encode(Sha256::digest(bytes))
}
fn error(status: StatusCode, code: &str) -> (StatusCode, ApiJson) {
    (
        status,
        ApiJson(json!({"error":{"code":code,"message":code}})),
    )
}
fn verify_json(bytes: &[u8], verifier: &Phase2LineageVerifier, action: bool) -> Value {
    let result = verifier.verify_v3(bytes);
    result_json(bytes, &result, action)
}
fn result_json(bytes: &[u8], result: &LineageResult, action: bool) -> Value {
    let obligations = result.obligations;
    let permission = TradePermission::from_result(result);
    let mut id_input = b"ZWA-EVIDENCE-v3:".to_vec();
    id_input.extend_from_slice(bytes);
    json!({
        "verificationId": hash(&id_input),
        "verification": result.to_json(),
        "lineagePermission": if permission.allowed { "ALLOWED" } else { "DENIED" },
        "tradePermission": {"allowed":permission.allowed,"reason":permission.reason},
        "eligibility": "NOT_EVALUATED",
        "settlement": "NOT_EXECUTED",
        "actionEvaluated": action,
        "timeline": [
            {"stage":"ISSUANCE_ROOT","status":obligations.root.as_str()},
            {"stage":"LINEAGE_STEPS","status":obligations.design_a.as_str()},
            {"stage":"CHAIN_CONTEXT","status":obligations.chain_context.as_str()},
            {"stage":"TERMINAL_OUTPUT","status":obligations.terminal.as_str()},
            {"stage":"FINALITY","status":obligations.finality.as_str()},
            {"stage":"TRADE_PERMISSION","status":if permission.allowed {"PASSED"} else {"FAILED"}}
        ],
        "trustSource": "PINNED_REGTEST_FIXTURE"
    })
}
fn parse_body(
    headers: &HeaderMap,
    body: &[u8],
    max_body: usize,
) -> Result<Vec<u8>, (StatusCode, ApiJson)> {
    if body.len() > max_body {
        return Err(error(StatusCode::PAYLOAD_TOO_LARGE, "REQUEST_TOO_LARGE"));
    }
    if headers.get(CONTENT_TYPE).and_then(|v| v.to_str().ok()) != Some("application/json") {
        return Err(error(
            StatusCode::UNSUPPORTED_MEDIA_TYPE,
            "CONTENT_TYPE_REQUIRED",
        ));
    }
    let req: VerifyRequest =
        serde_json::from_slice(body).map_err(|_| error(StatusCode::BAD_REQUEST, "INVALID_JSON"))?;
    if req.evidence_encoding != "base64" {
        return Err(error(StatusCode::BAD_REQUEST, "INVALID_ENCODING"));
    }
    if req.evidence.len() > MAX_EVIDENCE.div_ceil(3) * 4 + 4 {
        return Err(error(StatusCode::PAYLOAD_TOO_LARGE, "EVIDENCE_TOO_LARGE"));
    }
    let bytes = STANDARD
        .decode(req.evidence.as_bytes())
        .map_err(|_| error(StatusCode::BAD_REQUEST, "INVALID_BASE64"))?;
    if STANDARD.encode(&bytes) != req.evidence {
        return Err(error(StatusCode::BAD_REQUEST, "NONCANONICAL_BASE64"));
    }
    if bytes.len() > MAX_EVIDENCE {
        return Err(error(StatusCode::PAYLOAD_TOO_LARGE, "EVIDENCE_TOO_LARGE"));
    }
    Ok(bytes)
}
async fn run_verification(
    state: AppState,
    verifier: Arc<Phase2LineageVerifier>,
    bytes: Vec<u8>,
    action: bool,
    scenario: Option<&str>,
) -> Result<ApiJson, (StatusCode, ApiJson)> {
    let start = Instant::now();
    let permit = tokio::time::timeout(Duration::from_secs(30), state.limit.acquire_owned())
        .await
        .map_err(|_| error(StatusCode::SERVICE_UNAVAILABLE, "VERIFIER_BUSY"))?
        .map_err(|_| error(StatusCode::SERVICE_UNAVAILABLE, "VERIFIER_UNAVAILABLE"))?;
    let result = tokio::task::spawn_blocking(move || {
        let _permit = permit;
        verify_json(&bytes, &verifier, action)
    })
    .await
    .map_err(|_| error(StatusCode::INTERNAL_SERVER_ERROR, "INTERNAL_ERROR"))?;
    let v = &result["verification"];
    if state.log_level != "off" {
        println!(
            "{}",
            json!({"route":if scenario.is_some(){"demo"}else if action{"trade/authorize"}else{"lineage/verify"},"scenario":scenario,"snapshot":v["snapshot_id"],"status":v["status"],"code":v["code"],"steps":v["steps"],"durationMs":start.elapsed().as_millis()})
        );
    }
    Ok(ApiJson(result))
}
async fn live() -> ApiJson {
    ApiJson(json!({"status":"LIVE"}))
}
async fn ready(State(s): State<AppState>) -> ApiJson {
    ApiJson(
        json!({"status":"READY","snapshot":s.view.as_str(),"trustSource":"PINNED_REGTEST_FIXTURE"}),
    )
}
async fn info(State(s): State<AppState>) -> ApiJson {
    let v = VerifierIdentity::FROZEN;
    ApiJson(
        json!({"protocolVersion":"0.1.0","relation":v.relation,"verifierId":v.verifier_id,"vkDigest":hex::encode(v.vk_id),"parameterIdentity":hex::encode(v.params_sha256),"parameterK":v.params_k,"containerVersions":[3],"network":"regtest","historyModes":["FV"],"finality":{"mode":"depth","k":3},"snapshot":{"id":s.view.as_str(),"sha256":s.view.sha256()},"researchReview":"PENDING","implementationVersion":env!("CARGO_PKG_VERSION"),"trustSource":"PINNED_REGTEST_FIXTURE"}),
    )
}
async fn verify(
    State(s): State<AppState>,
    headers: HeaderMap,
    body: Result<Bytes, BytesRejection>,
) -> Result<ApiJson, (StatusCode, ApiJson)> {
    let start = Instant::now();
    let body = body.map_err(|_| error(StatusCode::PAYLOAD_TOO_LARGE, "REQUEST_TOO_LARGE"))?;
    let bytes = parse_body(&headers, &body, s.max_body)?;
    let parse_us = start.elapsed().as_micros();
    let ApiJson(mut result) =
        run_verification(s.clone(), s.production.clone(), bytes, false, None).await?;
    result["transportDecodeUs"] = json!(parse_us);
    result["totalHandlerUs"] = json!(start.elapsed().as_micros());
    Ok(ApiJson(result))
}
async fn authorize(
    State(s): State<AppState>,
    headers: HeaderMap,
    body: Result<Bytes, BytesRejection>,
) -> Result<ApiJson, (StatusCode, ApiJson)> {
    let start = Instant::now();
    let body = body.map_err(|_| error(StatusCode::PAYLOAD_TOO_LARGE, "REQUEST_TOO_LARGE"))?;
    let bytes = parse_body(&headers, &body, s.max_body)?;
    let parse_us = start.elapsed().as_micros();
    let ApiJson(mut result) =
        run_verification(s.clone(), s.production.clone(), bytes, true, None).await?;
    result["transportDecodeUs"] = json!(parse_us);
    result["totalHandlerUs"] = json!(start.elapsed().as_micros());
    Ok(ApiJson(result))
}
struct Scenario {
    id: &'static str,
    name: &'static str,
    description: &'static str,
    expected: &'static str,
    snapshot: &'static str,
    fixture: &'static str,
}
const SCENARIOS: [Scenario; 7] = [
    Scenario {
        id: "valid-rooted-n3",
        name: "A7 rooted N=3",
        description:
            "Authorized issuance, three real proofs, authenticated terminal and depth-3 finality",
        expected: "VERIFIED",
        snapshot: "A7",
        fixture: "n3-v3.bin",
    },
    Scenario {
        id: "pending-a4",
        name: "A4 pending finality",
        description: "The same N=3 evidence at tip 4",
        expected: "PENDING_FINALITY",
        snapshot: "A4",
        fixture: "n3-v3.bin",
    },
    Scenario {
        id: "checkpoint-ckn",
        name: "CKN checkpoint trust",
        description: "Checkpointed history without operator trust",
        expected: "CHECKPOINT_TRUST_REQUIRED",
        snapshot: "CKN",
        fixture: "ckn-v3.bin",
    },
    Scenario {
        id: "tampered-proof",
        name: "Tampered proof",
        description: "One byte changed inside the authoritative proof",
        expected: "REJECTED",
        snapshot: "A7",
        fixture: "n3-v3.bin",
    },
    Scenario {
        id: "bad-root",
        name: "Bad root authentication",
        description: "One byte changed in pinned root evidence",
        expected: "REJECTED",
        snapshot: "A7",
        fixture: "n3-v3.bin",
    },
    Scenario {
        id: "bad-terminal",
        name: "Bad terminal authentication",
        description: "One byte changed in pinned terminal evidence",
        expected: "REJECTED",
        snapshot: "A7",
        fixture: "n3-v3.bin",
    },
    Scenario {
        id: "reorg",
        name: "Invalidated reference",
        description: "A7 observed before switching to pinned B8",
        expected: "INVALIDATED_BY_REORG",
        snapshot: "B8",
        fixture: "n3-v3.bin",
    },
];
async fn scenarios() -> ApiJson {
    ApiJson(
        json!({"trustSource":"PINNED_REGTEST_FIXTURE","scenarios":SCENARIOS.iter().map(|x|json!({"id":x.id,"name":x.name,"description":x.description,"expected":x.expected,"snapshot":x.snapshot,"fixture":x.fixture})).collect::<Vec<_>>() }),
    )
}
async fn demo(
    State(s): State<AppState>,
    headers: HeaderMap,
    body: Result<Bytes, BytesRejection>,
) -> Result<ApiJson, (StatusCode, ApiJson)> {
    let body = body.map_err(|_| error(StatusCode::PAYLOAD_TOO_LARGE, "REQUEST_TOO_LARGE"))?;
    if body.len() > s.max_body {
        return Err(error(StatusCode::PAYLOAD_TOO_LARGE, "REQUEST_TOO_LARGE"));
    }
    if headers.get(CONTENT_TYPE).and_then(|v| v.to_str().ok()) != Some("application/json") {
        return Err(error(
            StatusCode::UNSUPPORTED_MEDIA_TYPE,
            "CONTENT_TYPE_REQUIRED",
        ));
    }
    let req: DemoRequest = serde_json::from_slice(&body)
        .map_err(|_| error(StatusCode::BAD_REQUEST, "INVALID_JSON"))?;
    let sc = SCENARIOS
        .iter()
        .find(|x| x.id == req.scenario_id)
        .ok_or_else(|| error(StatusCode::NOT_FOUND, "SCENARIO_NOT_FOUND"))?;
    let (verifier, mut bytes) = match sc.id {
        "pending-a4" => (s.a4.clone(), N3.to_vec()),
        "checkpoint-ckn" => (s.ckn.clone(), CKN.to_vec()),
        "reorg" => (s.reorg.clone(), N3.to_vec()),
        _ => (s.a7.clone(), N3.to_vec()),
    };
    let verifier =
        verifier.ok_or_else(|| error(StatusCode::SERVICE_UNAVAILABLE, "VERIFIER_UNAVAILABLE"))?;
    match sc.id {
        "tampered-proof" => bytes[2527 + 100] ^= 1,
        "bad-root" => bytes[230 + 100] ^= 1,
        "bad-terminal" => bytes[230 + 1169 + 100] ^= 1,
        _ => (),
    }
    let ApiJson(core) = run_verification(s.clone(), verifier, bytes, false, Some(sc.id)).await?;
    let observed = core["verification"]["status"]
        .as_str()
        .unwrap_or("INTERNAL_ERROR");
    let matched = observed == sc.expected;
    Ok(ApiJson(
        json!({"scenarioId":sc.id,"scenarioName":sc.name,"description":sc.description,"expected":sc.expected,"observed":observed,"matchesExpected":matched,"fixture":{"name":sc.fixture,"sha256":if sc.fixture=="ckn-v3.bin"{CKN_SHA}else{N3_SHA}},"snapshot":sc.snapshot,"demo":true,"result":core}),
    ))
}
fn pinned(name: SnapshotId) -> Result<Phase2LineageVerifier, String> {
    let view = SnapshotChainView::load(name)?;
    let config = Phase2VerifierConfig::new(
        view.network_id(),
        POLICY.to_vec(),
        hex::decode(POLICY_DIGEST)
            .map_err(|e| e.to_string())?
            .try_into()
            .map_err(|_| "policy digest size")?,
    );
    Phase2LineageVerifier::new(view, config)
}
fn check_sources() -> Result<(), String> {
    let lineage = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    for (dir, name) in [
        ("vendor/r2b-l1", "SOURCE_MANIFEST.json"),
        ("vendor/layer-b-core", "SOURCE_MANIFEST.json"),
    ] {
        let root = lineage.join(dir);
        let manifest: Value =
            serde_json::from_slice(&std::fs::read(root.join(name)).map_err(|e| e.to_string())?)
                .map_err(|e: serde_json::Error| e.to_string())?;
        for row in manifest["files"]
            .as_array()
            .ok_or("invalid source manifest")?
        {
            let path = row["path"]
                .as_str()
                .or_else(|| row["production_path"].as_str())
                .ok_or("invalid source path")?;
            let expected = row["packaged_sha256"]
                .as_str()
                .or_else(|| row["production_sha256"].as_str())
                .ok_or("invalid source hash")?;
            let local = if dir.ends_with("layer-b-core") {
                lineage.join(path)
            } else {
                root.join(path)
            };
            if hash(&std::fs::read(local).map_err(|e| e.to_string())?) != expected {
                return Err("source integrity failed".into());
            }
        }
    }
    Ok(())
}
fn from_env<T: std::str::FromStr>(name: &str, default: T) -> Result<T, String> {
    match env::var(name) {
        Ok(v) => v.parse().map_err(|_| format!("invalid {name}")),
        Err(env::VarError::NotPresent) => Ok(default),
        Err(_) => Err(format!("invalid {name}")),
    }
}
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let startup = Instant::now();
    check_sources()?;
    let policy = zwa_layer_b_core::policy::ProvenancePolicy::decode(POLICY)
        .ok_or("invalid pinned policy")?;
    if hex::encode(policy.digest()) != POLICY_DIGEST {
        return Err("policy digest mismatch".into());
    }
    for (bytes, pin) in [(POLICY, POLICY_SHA), (N3, N3_SHA), (CKN, CKN_SHA)] {
        if hash(bytes) != pin {
            return Err("fixture integrity failed".into());
        }
    }
    for id in [
        SnapshotId::A4,
        SnapshotId::A7,
        SnapshotId::Cpa,
        SnapshotId::B8,
        SnapshotId::Ckn,
    ] {
        SnapshotChainView::load(id)?;
    }
    let view = match env::var("ZWA_CHAIN_VIEW")?.as_str() {
        "A4" => SnapshotId::A4,
        "A7" => SnapshotId::A7,
        "CPA" => SnapshotId::Cpa,
        "B8" => SnapshotId::B8,
        "CKN" => SnapshotId::Ckn,
        _ => return Err("invalid ZWA_CHAIN_VIEW".into()),
    };
    let demo_enabled = from_env("ZWA_DEMO_ENABLED", false)?;
    let concurrent: usize = from_env("ZWA_MAX_CONCURRENT", 2)?;
    if concurrent == 0 || concurrent > 16 {
        return Err("invalid ZWA_MAX_CONCURRENT".into());
    }
    let max_body: usize = from_env("ZWA_MAX_BODY_BYTES", DEFAULT_MAX_BODY)?;
    if !(25_000..=16_000_000).contains(&max_body) {
        return Err("invalid ZWA_MAX_BODY_BYTES".into());
    }
    let log_level = env::var("ZWA_LOG_LEVEL").unwrap_or_else(|_| "info".into());
    if !["off", "info"].contains(&log_level.as_str()) {
        return Err("invalid ZWA_LOG_LEVEL".into());
    }
    let bind: SocketAddr = env::var("ZWA_BIND")
        .unwrap_or_else(|_| "127.0.0.1:3000".into())
        .parse()?;
    let prod = Arc::new(pinned(view)?);
    let (a7, a4, ckn, reorg) = if demo_enabled {
        let a7 = Arc::new(pinned(SnapshotId::A7)?);
        let a4 = Arc::new(pinned(SnapshotId::A4)?);
        let ckn = Arc::new(pinned(SnapshotId::Ckn)?);
        let mut reorg = pinned(SnapshotId::A7)?;
        if reorg.verify_v3(N3).status.as_str() != "VERIFIED" {
            return Err("reorg seed failed".into());
        }
        reorg.set_view(SnapshotChainView::load(SnapshotId::B8)?)?;
        (Some(a7), Some(a4), Some(ckn), Some(Arc::new(reorg)))
    } else {
        (None, None, None, None)
    };
    let state = AppState {
        production: prod,
        a7,
        a4,
        ckn,
        reorg,
        view,
        limit: Arc::new(Semaphore::new(concurrent)),
        max_body,
        log_level,
    };
    let mut app = Router::new()
        .route("/health/live", get(live))
        .route("/health/ready", get(ready))
        .route("/v1/verifier/info", get(info))
        .route("/v1/lineage/verify", post(verify))
        .route("/v1/trade/authorize", post(authorize));
    if demo_enabled {
        app = app
            .route("/v1/demo/scenarios", get(scenarios))
            .route("/v1/demo/run", post(demo));
    }
    let app = app.layer(DefaultBodyLimit::max(max_body)).with_state(state);
    let listener = tokio::net::TcpListener::bind(bind).await?;
    println!(
        "{}",
        json!({"event":"started","bind":bind.to_string(),"snapshot":view.as_str(),"demo":demo_enabled,"startupMs":startup.elapsed().as_millis()})
    );
    axum::serve(listener, app).await?;
    Ok(())
}
