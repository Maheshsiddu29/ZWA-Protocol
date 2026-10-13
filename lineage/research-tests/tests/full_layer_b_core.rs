//! Layer-B adversarial matrix.
//!
//! `lb_matrix --run DIR --fixtures DIR`
//!
//! Loads the verifier views captured in `DIR/views` (sha256 pinned by
//! `DIR/views/SHA256SUMS`), the frozen fixtures, and runs every vector. Each row
//! has an expected outcome fixed here before execution:
//!   `ACCEPT`, an exact failure code, `REJECT:*` (any rejection), or `FACT`
//!   (a checked statement that must be true).
//! Writes `DIR/matrix.jsonl` and `DIR/matrix.txt`. No randomness: two runs
//! over byte-identical views must give byte-identical output.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::panic
)]

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use ff::PrimeField;
use orchard::issuance::auth::IssueValidatingKey;
use orchard::note::ExtractedNoteCommitment;
use orchard::Anchor;
use pasta_curves::pallas;
use serde_json::{json, Value};
use zwa_lineage_r2b_l1::public_inputs::PublicInputsV2;
use zwa_r3e_experiments::design_a::{self as research_da, Edge, Mode, Reject};

use zwa_layer_b::build::{asset_of, desc_hash, Keys as BuildKeys, H1B_SEED_PHRASE};
use zwa_layer_b::chainref::{ChainRef, NetworkKind, NetworkParams, ACTION_VK_ID, RULESET_TAG};
use zwa_layer_b::finality::{validate_chain_ref, validate_identity, validate_policy, Tracker};
use zwa_layer_b::object::{accept_rooted_lineage, Keys, LineageObject};
use zwa_layer_b::policy::{
    Code, FinalityMode, PolicyEntry, ProvenancePolicy, VerifierConfig, OPEN_END, STATUS_ACTIVE,
    STATUS_REVOKED,
};
use zwa_layer_b::root::{authorize_root, make_evidence as root_ev};
use zwa_layer_b::terminal::{authenticate_terminal, make_evidence as term_ev, TerminalEvidence};
use zwa_layer_b::util::{blake2b_256, from_display, h, sha256, sha256_hex, unhex32, H32};
use zwa_layer_b::view::{root_of, tx_leaves, ChainView, LeafKind};
use zwa_layer_b_core as core;

// The frozen matrix below is copied from lb_matrix.rs at research HEAD
// 3e7115d. These conversion functions make its exact adversarial inputs run
// through the production-only core as well as the frozen research crate.
fn core_kind(k: NetworkKind) -> core::chainref::NetworkKind {
    match k {
        NetworkKind::Mainnet => core::chainref::NetworkKind::Mainnet,
        NetworkKind::Testnet => core::chainref::NetworkKind::Testnet,
        NetworkKind::Regtest => core::chainref::NetworkKind::Regtest,
    }
}

fn core_view(v: &ChainView) -> core::view::ChainView {
    core::view::ChainView {
        label: v.label.clone(),
        snapshot_sha256: v.snapshot_sha256.clone(),
        params: core::chainref::NetworkParams {
            kind: core_kind(v.params.kind),
            genesis_hash: v.params.genesis_hash,
            upgrades: v.params.upgrades.clone(),
            action_vk_id: v.params.action_vk_id,
            ruleset_tag: v.params.ruleset_tag.clone(),
        },
        network_id: v.network_id,
        tip: v.tip,
        hashes: v.hashes.clone(),
        roots: v.roots.clone(),
        sizes: v.sizes.clone(),
        blocks: v.blocks.clone(),
        leaves: v
            .leaves
            .iter()
            .map(|l| core::view::Leaf {
                position: l.position,
                height: l.height,
                tx_index: l.tx_index,
                txid: l.txid,
                kind: match l.kind {
                    LeafKind::Action { action_index } => {
                        core::view::LeafKind::Action { action_index }
                    }
                    LeafKind::IssueNote {
                        action_index,
                        note_index,
                    } => core::view::LeafKind::IssueNote {
                        action_index,
                        note_index,
                    },
                },
                cmx: l.cmx,
            })
            .collect(),
        node_checkpoint_max: v.node_checkpoint_max,
    }
}

fn core_config(c: &VerifierConfig) -> core::policy::VerifierConfig {
    use core::policy::FinalityMode as F;
    core::policy::VerifierConfig {
        kind: core_kind(c.kind),
        action_vk_id: c.action_vk_id,
        ruleset_tag: c.ruleset_tag.clone(),
        allowed_networks: c.allowed_networks.clone(),
        policy_bytes: c.policy_bytes.clone(),
        accepted_policy_digests: c.accepted_policy_digests.clone(),
        revoked_policy_digests: c.revoked_policy_digests.clone(),
        finality: match c.finality {
            FinalityMode::Depth(k) => F::Depth(k),
            FinalityMode::Checkpoint => F::Checkpoint,
            FinalityMode::Hybrid(k) => F::Hybrid(k),
        },
        trusted_checkpoints: c.trusted_checkpoints.clone(),
        max_age: c.max_age,
        accept_checkpointed_history: c.accept_checkpointed_history,
    }
}

fn core_tracker(t: &Tracker, encoded: &[u8]) -> core::finality::Tracker {
    use core::finality::RefState as S;
    let mut out = core::finality::Tracker::default();
    if let Ok(r) = ChainRef::decode(encoded) {
        if let Some(previous) = t.previous(&r) {
            let state = match previous {
                zwa_layer_b::finality::RefState::Unknown => S::Unknown,
                zwa_layer_b::finality::RefState::Accepted => S::Accepted,
                zwa_layer_b::finality::RefState::Finalized => S::Finalized,
                zwa_layer_b::finality::RefState::Invalidated => S::Invalidated,
                zwa_layer_b::finality::RefState::FinalityViolation => S::FinalityViolation,
            };
            out.record(&core::chainref::ChainRef::decode(encoded).unwrap(), state);
        }
    }
    out
}

fn outcome_code<T, E: std::fmt::Display>(r: &Result<T, E>) -> String {
    match r {
        Ok(_) => "ACCEPT".into(),
        Err(e) => e.to_string(),
    }
}

fn paired_load(bytes: &[u8], kind: NetworkKind, vk: H32, tag: &str) -> Result<ChainView, String> {
    let frozen = ChainView::load(bytes, kind, vk, tag);
    let imported = core::view::ChainView::load(bytes, core_kind(kind), vk, tag);
    assert_eq!(
        outcome_code(&frozen),
        outcome_code(&imported),
        "view loader differential"
    );
    if let (Ok(a), Ok(b)) = (&frozen, &imported) {
        assert_eq!(
            (
                a.network_id,
                a.hashes.as_slice(),
                a.roots.as_slice(),
                a.sizes.as_slice(),
                a.node_checkpoint_max
            ),
            (
                b.network_id,
                b.hashes.as_slice(),
                b.roots.as_slice(),
                b.sizes.as_slice(),
                b.node_checkpoint_max
            )
        );
    }
    record_production("TRUE".into());
    frozen
}

type Outcome = Result<String, Code>;
static LAST_COMPARISON: Mutex<Option<String>> = Mutex::new(None);

fn record_production(s: String) {
    *LAST_COMPARISON.lock().unwrap() = Some(s);
}

struct M {
    rows: Vec<Value>,
    fails: usize,
}

impl M {
    fn row(&mut self, id: &str, group: &str, desc: &str, expected: &str, got: Outcome) {
        let production_result = LAST_COMPARISON.lock().unwrap().take();
        let actual = match &got {
            Ok(_) => "ACCEPT".to_string(),
            Err(c) => c.to_string(),
        };
        let pass = match expected {
            "ACCEPT" => got.is_ok(),
            "REJECT:*" => got.is_err(),
            e => actual == e,
        };
        let detail = match &got {
            Ok(d) => d.clone(),
            Err(_) => String::new(),
        };
        self.push(
            id,
            group,
            desc,
            expected,
            &actual,
            production_result.as_deref(),
            pass,
            &detail,
        );
    }
    fn fact(&mut self, id: &str, group: &str, desc: &str, ok: bool, detail: &str) {
        let production_result = if id.starts_with("VW-") {
            LAST_COMPARISON.lock().unwrap().take()
        } else {
            None
        };
        self.push(
            id,
            group,
            desc,
            "FACT",
            if ok { "TRUE" } else { "FALSE" },
            production_result.as_deref(),
            ok,
            detail,
        );
    }
    #[allow(clippy::too_many_arguments)]
    fn push(
        &mut self,
        id: &str,
        group: &str,
        desc: &str,
        expected: &str,
        actual: &str,
        production_result: Option<&str>,
        pass: bool,
        detail: &str,
    ) {
        if !pass {
            self.fails += 1;
        }
        println!(
            "[{}] {id} {desc}: expected {expected}, got {actual}{}",
            if pass { "PASS" } else { "FAIL" },
            if detail.is_empty() {
                String::new()
            } else {
                format!(" ({detail})")
            }
        );
        self.rows.push(json!({ "id": id, "group": group, "vector": desc, "expected": expected, "actual": actual,
            "production_result": production_result, "verdict": if pass { "PASS" } else { "FAIL" }, "detail": detail }));
    }
}

struct Ctx {
    run: PathBuf,
    fx: PathBuf,
    sums: BTreeMap<String, String>,
    notes: Value,
    proofs: Value,
}

impl Ctx {
    fn view_bytes(&self, name: &str) -> Vec<u8> {
        fs::read(self.run.join("views").join(format!("{name}.json"))).unwrap()
    }
    /// Loads a view the way the verifier does: pinned sha256, then validation.
    fn view(&self, name: &str) -> Result<ChainView, String> {
        let b = self.view_bytes(name);
        let pinned = self
            .sums
            .get(&format!("{name}.json"))
            .ok_or("view not pinned")?;
        if &sha256_hex(&b) != pinned {
            return Err("snapshot sha256 differs from the pinned value".into());
        }
        paired_load(&b, NetworkKind::Regtest, unhex32(ACTION_VK_ID), RULESET_TAG)
    }
    fn cmx(&self, note: &str) -> H32 {
        unhex32(
            self.notes[note]["cmx"]
                .as_str()
                .unwrap_or_else(|| panic!("note {note}")),
        )
    }
    fn step_x(&self, step: &str) -> PublicInputsV2 {
        let s = &self.proofs["steps"][step];
        PublicInputsV2 {
            cmx_old: unhex32(s["cmx_old"].as_str().unwrap()),
            nf_old: unhex32(s["nf_old"].as_str().unwrap()),
            cmx_new: unhex32(s["cmx_new"].as_str().unwrap()),
        }
    }
    fn edges(&self, steps: &[&str]) -> Vec<Edge> {
        steps
            .iter()
            .map(|s| Edge {
                x: self.step_x(s),
                proof: fs::read(self.fx.join("proofs").join(format!("{s}.bin"))).unwrap(),
            })
            .collect()
    }
}

fn cref(v: &ChainView, height: u32) -> ChainRef {
    ChainRef {
        network_id: v.network_id,
        height,
        block_hash: v.hashes[height as usize],
        orchard_root: v.roots[height as usize].unwrap(),
    }
}

fn pos(v: &ChainView, cmx: &H32) -> u64 {
    *v.find(cmx)
        .first()
        .unwrap_or_else(|| panic!("{} not a leaf of {}", h(cmx), v.label))
}

fn ik_bytes(
    isk: &orchard::issuance::auth::IssueAuthKey<orchard::issuance::auth::ZSASchnorr>,
) -> [u8; 33] {
    IssueValidatingKey::from(isk).encode().try_into().unwrap()
}

fn entry(
    isk: &orchard::issuance::auth::IssueAuthKey<orchard::issuance::auth::ZSASchnorr>,
    desc: &str,
    from: u32,
    status: u8,
) -> PolicyEntry {
    PolicyEntry {
        issuer_ik: ik_bytes(isk),
        asset_desc_hash: desc_hash(desc),
        asset_base: asset_of(isk, desc).to_bytes(),
        valid_from: from,
        valid_until: OPEN_END,
        status,
    }
}

fn policy(network_id: H32, entries: Vec<PolicyEntry>) -> ProvenancePolicy {
    ProvenancePolicy {
        policy_id: blake2b_256(b"ZWA_LB_PolicyDig", b"ZWA Layer-B research policy v1"),
        network_id,
        entries,
    }
}

fn config(network_id: H32, p: &ProvenancePolicy) -> VerifierConfig {
    VerifierConfig {
        kind: NetworkKind::Regtest,
        action_vk_id: unhex32(ACTION_VK_ID),
        ruleset_tag: RULESET_TAG.to_string(),
        allowed_networks: vec![network_id],
        policy_bytes: Some(p.encode()),
        accepted_policy_digests: vec![p.digest()],
        revoked_policy_digests: vec![],
        finality: FinalityMode::Depth(3),
        trusted_checkpoints: vec![],
        max_age: None,
        accept_checkpointed_history: true,
    }
}

/// Root check under a reference: identity, AuthorizeRoot, then policy.
fn root_check(
    root_cmx: &H32,
    ev: &[u8],
    r: &ChainRef,
    v: &ChainView,
    cfg: &VerifierConfig,
    t: &mut Tracker,
) -> Outcome {
    let cv = core_view(v);
    let cc = core_config(cfg);
    let mut ct = core_tracker(t, &r.encode());
    let imported: Result<String, core::policy::Code> = (|| {
        let cr = core::finality::validate_identity(&r.encode(), Some(&cv), &cc, &ct)?;
        let info = core::root::authorize_root(root_cmx, ev, &cr, &cv, &cc)?;
        core::finality::validate_policy(&cr, &cv, &cc, &mut ct)?;
        Ok(format!(
            "root h={} pos={} value={}",
            info.height, info.position, info.value
        ))
    })();
    let frozen: Outcome = (|| {
        let rr = validate_identity(&r.encode(), Some(v), cfg, t)?;
        let info = authorize_root(root_cmx, ev, &rr, v, cfg)?;
        validate_policy(&rr, v, cfg, t)?;
        Ok(format!(
            "root h={} pos={} value={}",
            info.height, info.position, info.value
        ))
    })();
    assert_eq!(
        outcome_code(&frozen),
        outcome_code(&imported),
        "root differential"
    );
    record_production(outcome_code(&imported));
    frozen
}

fn term_check(
    term_cmx: &H32,
    ev: &[u8],
    r: &ChainRef,
    v: &ChainView,
    cfg: &VerifierConfig,
    t: &mut Tracker,
    keys: &Keys,
) -> Outcome {
    let cv = core_view(v);
    let cc = core_config(cfg);
    let mut ct = core_tracker(t, &r.encode());
    let imported: Result<String, core::policy::Code> = (|| {
        let cr = core::finality::validate_identity(&r.encode(), Some(&cv), &cc, &ct)?;
        let info = core::terminal::authenticate_terminal(term_cmx, ev, &cr, &cv, &keys.action_vk)?;
        core::finality::validate_policy(&cr, &cv, &cc, &mut ct)?;
        Ok(format!(
            "terminal h={} pos={} txid={} action={}",
            info.height,
            info.position,
            core::util::to_display(&info.txid),
            info.action_index
        ))
    })();
    let frozen: Outcome = (|| {
        let rr = validate_identity(&r.encode(), Some(v), cfg, t)?;
        let info = authenticate_terminal(term_cmx, ev, &rr, v, &keys.action_vk)?;
        validate_policy(&rr, v, cfg, t)?;
        Ok(format!(
            "terminal h={} pos={} txid={} action={}",
            info.height,
            info.position,
            zwa_layer_b::util::to_display(&info.txid),
            info.action_index
        ))
    })();
    assert_eq!(
        outcome_code(&frozen),
        outcome_code(&imported),
        "terminal differential"
    );
    record_production(outcome_code(&imported));
    frozen
}

fn cr_check(r: &[u8], v: Option<&ChainView>, cfg: &VerifierConfig, t: &mut Tracker) -> Outcome {
    let cv = v.map(core_view);
    let cc = core_config(cfg);
    let mut ct = core_tracker(t, r);
    let imported = core::finality::validate_chain_ref(r, cv.as_ref(), &cc, &mut ct)
        .map(|(rr, s)| format!("h={} {:?}", rr.height, s));
    let frozen = validate_chain_ref(r, v, cfg, t).map(|(rr, s)| format!("h={} {:?}", rr.height, s));
    assert_eq!(
        outcome_code(&frozen),
        outcome_code(&imported),
        "chainref differential"
    );
    record_production(outcome_code(&imported));
    frozen
}

fn obj_check(
    o: &LineageObject,
    v: Option<&ChainView>,
    cfg: &VerifierConfig,
    t: &mut Tracker,
    keys: &Keys,
) -> Outcome {
    raw_obj_check(&o.encode(), v, cfg, t, keys)
}

fn raw_obj_check(
    encoded: &[u8],
    v: Option<&ChainView>,
    cfg: &VerifierConfig,
    t: &mut Tracker,
    keys: &Keys,
) -> Outcome {
    let cv = v.map(core_view);
    let cc = core_config(cfg);
    let mut ct = core_tracker(
        t,
        LineageObject::decode(encoded)
            .as_ref()
            .map_or(&[][..], |o| o.chain_ref.as_slice()),
    );
    let imported: Result<String, String> = (|| {
        let parsed = LineageObject::decode(encoded).ok_or("MALFORMED".to_string())?;
        let cr = core::finality::validate_identity(&parsed.chain_ref, cv.as_ref(), &cc, &ct)
            .map_err(|e| e.to_string())?;
        let view = cv.as_ref().unwrap();
        let root =
            core::root::authorize_root(&parsed.root_cmx, &parsed.root_evidence, &cr, view, &cc)
                .map_err(|e| e.to_string())?;
        if !keys.r2b_vk_ok {
            return Err("DA_VK_IDENTITY".into());
        }
        research_da::verify(
            &keys.params,
            &keys.r2b_vk,
            &parsed.root_cmx,
            &parsed.terminal_cmx,
            &parsed.edges,
            Mode::Single,
        )
        .map_err(|e| match e {
            Reject::Empty => "DA_EMPTY".into(),
            Reject::NonCanonical(i) => format!("DA_NONCANONICAL({i})"),
            Reject::Proof(i) => format!("DA_PROOF({i})"),
            Reject::Batch => format!("DA_PROOF({})", usize::MAX),
            Reject::Join(i) => format!("DA_JOIN({i})"),
            Reject::Root => "DA_ROOT".into(),
            Reject::Terminal => "DA_TERMINAL".into(),
        })?;
        let terminal = core::terminal::authenticate_terminal(
            &parsed.terminal_cmx,
            &parsed.terminal_evidence,
            &cr,
            view,
            &keys.action_vk,
        )
        .map_err(|e| e.to_string())?;
        if !(root.height < terminal.height && terminal.height <= cr.height) {
            return Err("CONTEXT".into());
        }
        core::finality::validate_policy(&cr, view, &cc, &mut ct).map_err(|e| e.to_string())?;
        Ok("ACCEPT".into())
    })();
    let frozen = accept_rooted_lineage(encoded, v, cfg, t, keys).map(|a| {
        format!(
            "N={} root h={} terminal h={} txid={} action={} ref h={} {:?} ckpt_inherited={}",
            a.n,
            a.root.height,
            a.terminal.height,
            zwa_layer_b::util::to_display(&a.terminal.txid),
            a.terminal.action_index,
            a.chain_ref.height,
            a.state,
            a.ckpt_inherited
        )
    });
    assert_eq!(
        outcome_code(&frozen),
        outcome_code(&imported),
        "combined differential"
    );
    record_production(outcome_code(&imported));
    frozen
}

fn flip(mut b: H32, byte: usize) -> H32 {
    b[byte] ^= 0x01;
    b
}

fn q_p_le() -> H32 {
    // q_P = 0x40000000000000000000000000000000224698fc094cf91b992d30ed00000001
    let mut b = [0u8; 32];
    let be =
        hex::decode("40000000000000000000000000000000224698fc094cf91b992d30ed00000001").unwrap();
    for i in 0..32 {
        b[i] = be[31 - i];
    }
    b
}

/// x + q_P as 32 little-endian bytes (an alias of a canonical encoding).
fn plus_q(x: &H32) -> H32 {
    let q = q_p_le();
    let mut out = [0u8; 32];
    let mut carry = 0u16;
    for i in 0..32 {
        let s = x[i] as u16 + q[i] as u16 + carry;
        out[i] = s as u8;
        carry = s >> 8;
    }
    out
}

#[test]
fn full_layer_b_core_differential() {
    let research = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../ZWA-Lineage-Research/post-h1f/layer-b");
    let run = research.join("raw/runs/run3");
    let fx = research.join("fixtures/new");
    let sums: BTreeMap<String, String> = fs::read_to_string(run.join("views/SHA256SUMS"))
        .unwrap()
        .lines()
        .map(|l| {
            let mut it = l.split_whitespace();
            let s = it.next().unwrap().to_string();
            (it.next().unwrap().to_string(), s)
        })
        .collect();
    let c = Ctx {
        run: run.clone(),
        fx: fx.clone(),
        sums,
        notes: serde_json::from_str(&fs::read_to_string(fx.join("notes.json")).unwrap()).unwrap(),
        proofs: serde_json::from_str(&fs::read_to_string(fx.join("proofs/manifest.json")).unwrap())
            .unwrap(),
    };
    let mut m = M {
        rows: vec![],
        fails: 0,
    };
    let keys = Keys::load();
    m.fact(
        "KEY-R2B",
        "setup",
        "fresh keygen of R2B-L1 has the frozen VK identity a2b349d7…36b3",
        keys.r2b_vk_ok,
        "",
    );

    // ================= VW: views =================
    let names = [
        "A4", "A7", "B8", "B8R", "CPA", "CPX", "H1B0", "H1B2A", "H1C", "NET",
    ];
    let mut views: BTreeMap<&str, ChainView> = BTreeMap::new();
    for n in names {
        let r = c.view(n);
        let ok = r.is_ok();
        let detail = match &r {
            Ok(v) => format!(
                "tip {} leaves {} node_checkpoint_max {} network_id {}",
                v.tip,
                v.leaves.len(),
                v.node_checkpoint_max,
                &h(&v.network_id)[..16]
            ),
            Err(e) => e.clone(),
        };
        m.fact(&format!("VW-LOAD-{n}"), "view", &format!("view {n}: header hashes, prev_block links, genesis and every Orchard root re-derived from block bytes equal the node's"), ok, &detail);
        if let Ok(v) = r {
            views.insert(n, v);
        }
    }
    let va7 = &views["A7"];
    {
        let base = c.view_bytes("A7");
        let mut snap: Value = serde_json::from_slice(&base).unwrap();
        let try_load = |s: &Value| {
            paired_load(
                &serde_json::to_vec_pretty(s).unwrap(),
                NetworkKind::Regtest,
                unhex32(ACTION_VK_ID),
                RULESET_TAG,
            )
        };
        let mut s1 = snap.clone();
        let hx = s1["blocks"][3]["hex"].as_str().unwrap().to_string();
        let mut bytes = hex::decode(&hx).unwrap();
        let k = bytes.len() - 200;
        bytes[k] ^= 1;
        s1["blocks"][3]["hex"] = json!(hex::encode(bytes));
        let r = try_load(&s1);
        m.fact("VW-TAMPER-BLOCK", "view", "a flipped byte inside block 3 of the verifier's snapshot makes the whole view fail to load", r.is_err(), &r.err().unwrap_or_default());
        // Effecting data of the issuance transaction ISS_X (an issue-note value
        // byte) and authorizing data (its issueAuthSig).
        let a1 = hex::decode(snap["blocks"][1]["hex"].as_str().unwrap()).unwrap();
        let pb = zwa_layer_b::util::parse_block(&a1).unwrap();
        let issx = pb.txs.iter().find(|t| t.issue_bundle().is_some()).unwrap();
        let txb = zwa_layer_b::util::tx_bytes(issx);
        let at = a1
            .windows(txb.len())
            .position(|w| w == txb.as_slice())
            .unwrap();
        let enc_sig = issx
            .issue_bundle()
            .unwrap()
            .authorization()
            .signature()
            .sig()
            .encode();
        let sig = enc_sig[enc_sig.len() - 64..].to_vec();
        let sig_at = at
            + txb
                .windows(sig.len())
                .position(|w| w == sig.as_slice())
                .unwrap();
        let mut s6 = snap.clone();
        let mut b6 = a1.clone();
        b6[sig_at + sig.len() - 1] ^= 1;
        s6["blocks"][1]["hex"] = json!(hex::encode(&b6));
        let r6 = try_load(&s6);
        m.fact("VW-TAMPER-AUTH", "view", "a flipped byte in ISS_X's issueAuthSig (authorizing data, outside the txid) still loads: the header binds it only through hashBlockCommitments", r6.is_ok(), &r6.as_ref().err().cloned().unwrap_or_default());
        if let Ok(v6) = r6 {
            let x0c = c.cmx("x0");
            let e = root_ev(&v6, pos(&v6, &x0c), 4);
            let cfg6 = config(
                v6.network_id,
                &policy(
                    v6.network_id,
                    vec![entry(
                        &BuildKeys::research().i1,
                        "ZWA-LB-ASSET-X",
                        0,
                        STATUS_ACTIVE,
                    )],
                ),
            );
            let got = root_check(
                &x0c,
                &e.encode(),
                &cref(&v6, 4),
                &v6,
                &cfg6,
                &mut Tracker::default(),
            );
            m.row(
                "VW-TAMPER-AUTH-ROOT",
                "view",
                "x0 from that view: Layer B's own issueAuthSig check fails closed",
                "ROOT_ISSUANCE_INVALID",
                got,
            );
        }
        let nf_bytes = match issx.orchard_bundle().unwrap() {
            zcash_primitives::transaction::OrchardBundle::OrchardZSA(b) => {
                b.actions().first().nullifier().to_bytes()
            }
            _ => unreachable!(),
        };
        let nf_at = at + txb.windows(32).position(|w| w == nf_bytes).unwrap();
        let mut s7 = snap.clone();
        let mut b7 = a1.clone();
        b7[nf_at] ^= 1;
        s7["blocks"][1]["hex"] = json!(hex::encode(&b7));
        let r = try_load(&s7);
        m.fact("VW-TAMPER-TX", "view", "a flipped byte in ISS_X's effecting data (its first nullifier) fails the header's transaction Merkle root", r.as_ref().err().map(|e| e.contains("Merkle root")).unwrap_or(false), &r.err().unwrap_or_default());
        let mut s2 = snap.clone();
        s2["blocks"][4]["orchard_final_root"] = s2["blocks"][3]["orchard_final_root"].clone();
        let r = try_load(&s2);
        m.fact(
            "VW-TAMPER-ROOT",
            "view",
            "a node finalRoot replaced by another height's root fails the recomputation",
            r.is_err(),
            &r.err().unwrap_or_default(),
        );
        let mut s3 = snap.clone();
        s3["blocks"][3]["hash"] = s3["blocks"][2]["hash"].clone();
        let r = try_load(&s3);
        m.fact(
            "VW-TAMPER-HASH",
            "view",
            "a node block hash that is not the header's hash fails",
            r.is_err(),
            &r.err().unwrap_or_default(),
        );
        let mut s4 = snap.clone();
        s4["blocks"].as_array_mut().unwrap().pop();
        let r = try_load(&s4);
        m.fact(
            "VW-TRUNCATED",
            "view",
            "a snapshot missing its tip block fails",
            r.is_err(),
            &r.err().unwrap_or_default(),
        );
        let r = paired_load(
            &base,
            NetworkKind::Testnet,
            unhex32(ACTION_VK_ID),
            RULESET_TAG,
        );
        m.fact("VW-KIND", "view", "a regtest snapshot loaded under a testnet verifier configuration fails (genesis / kind)", r.is_err(), &r.err().unwrap_or_default());
        let mut s5 = snap.clone();
        s5["blocks"][2]["hex"] = snap["blocks"][3]["hex"].clone();
        s5["blocks"][2]["hash"] = snap["blocks"][3]["hash"].clone();
        let r = try_load(&s5);
        m.fact(
            "VW-LINK",
            "view",
            "a block swapped in at another height fails the prev_block linkage",
            r.is_err(),
            &r.err().unwrap_or_default(),
        );
        snap["label"] = json!("A7-relabelled");
        let pinned = c.sums.get("A7.json").unwrap();
        let ok = &sha256_hex(&serde_json::to_vec_pretty(&snap).unwrap()) != pinned;
        assert!(zwa_lineage::phase2::SnapshotChainView::load_bytes(
            zwa_lineage::phase2::SnapshotId::A7,
            &serde_json::to_vec_pretty(&snap).unwrap(),
        )
        .is_err());
        record_production("TRUE".into());
        m.fact("VW-PIN", "view", "any change to a snapshot changes its sha256, so the verifier's pinned digest rejects it", ok, "");
        let a =
            views["B8"].hashes == views["B8R"].hashes && views["B8"].roots == views["B8R"].roots;
        m.fact("VW-B9R", "view", "submitting the rejected block B9R left the node's chain unchanged (B8 and B8R views have equal hashes and roots)", a, "");
        let empty = Anchor::empty_tree().to_bytes();
        let nv = &views["NET"];
        m.fact(
            "VW-EMPTY-ROOT",
            "view",
            "NET height 1 (no Orchard leaves): node finalRoot equals the empty-tree root",
            nv.roots[1] == Some(empty) && nv.sizes[1] == 0,
            &h(&empty),
        );
        let hv = &views["H1C"];
        let frozen = hv.hashes[1]
            == from_display("8ec682d5c526d98c1b5a6a92805367e4d04757b973c6f75d6a163b3e21d7d2e9")
            && hv.hashes[2]
                == from_display("6259553f9c74ab325fe167bbe55dd4cc034490d0eefba269979cfc178770949b")
            && hv.roots[1]
                == Some(unhex32(
                    "3d496ee452d08da58481faf9a8a327e931fb2bb10d56510122dbd9859195343f",
                ))
            && hv.roots[2]
                == Some(unhex32(
                    "364c6578febd47b358f15c49961b7f70441f6976f3fa441df68616f6473dca20",
                ));
        m.fact("VW-H1C-FROZEN", "view", "replayed frozen H1C blocks have the frozen hashes and Orchard roots (r2c-h1c/fixture/cross-check.json)", frozen, "");
        let snapj: Value = serde_json::from_slice(&base).unwrap();
        m.fact(
            "VW-CHAIN-NAME",
            "view",
            "the regtest node reports chain name \"test\": the name cannot identify the network",
            snapj["node_reported_chain"] == "test",
            "",
        );
        // Three implementations agree: node finalRoot (zebra-chain), bridgetree, MerklePath::root.
        let mut agree = 0;
        let mut total = 0;
        for v in views.values() {
            for hh in 1..=v.tip {
                if let Some(root) = v.roots[hh as usize] {
                    let n = v.sizes[hh as usize] as usize;
                    let leaves: Vec<H32> = v.leaves[..n].iter().map(|l| l.cmx).collect();
                    total += 1;
                    if root_of(&leaves) == root {
                        agree += 1;
                    }
                }
            }
            for l in &v.leaves {
                let p = v.witness(l.position, v.tip).unwrap();
                total += 1;
                if Some(
                    p.root(ExtractedNoteCommitment::from_bytes(&l.cmx).unwrap())
                        .to_bytes(),
                ) == v.roots[v.tip as usize]
                {
                    agree += 1;
                }
            }
        }
        m.fact("VW-3IMPL", "view", "Zebra finalRoot = bridgetree root at every height, and MerklePath::root(leaf) = tip root for every leaf of every view", agree == total, &format!("{agree}/{total}"));
    }

    // ================= Network ids and policies =================
    let net_a = va7.network_id;
    let net_b = views["NET"].network_id;
    let mk = |kind, genesis: &str, ups: Vec<(u32, u32)>, vk: H32, tag: &str| {
        NetworkParams {
            kind,
            genesis_hash: from_display(genesis),
            upgrades: ups,
            action_vk_id: vk,
            ruleset_tag: tag.into(),
        }
        .network_id()
    };
    let ups_a = va7.params.upgrades.clone();
    let net_main = mk(
        NetworkKind::Mainnet,
        zwa_layer_b::chainref::GENESIS_MAINNET,
        ups_a.clone(),
        unhex32(ACTION_VK_ID),
        RULESET_TAG,
    );
    let net_test = mk(
        NetworkKind::Testnet,
        zwa_layer_b::chainref::GENESIS_TESTNET,
        ups_a.clone(),
        unhex32(ACTION_VK_ID),
        RULESET_TAG,
    );
    let net_tag = mk(
        NetworkKind::Regtest,
        zwa_layer_b::chainref::GENESIS_REGTEST,
        ups_a.clone(),
        unhex32(ACTION_VK_ID),
        "zebra=0aef55ce;orchard=d91aaf14;features=swap",
    );
    let net_vk = mk(
        NetworkKind::Regtest,
        zwa_layer_b::chainref::GENESIS_REGTEST,
        ups_a.clone(),
        sha256(b"another Action circuit description"),
        RULESET_TAG,
    );
    let ids = [net_a, net_b, net_main, net_test, net_tag, net_vk];
    let distinct = ids.iter().collect::<std::collections::BTreeSet<_>>().len() == ids.len();
    m.fact("CR-NETIDS", "chainref", "network_id differs for regtest-A, regtest-B (NU7 at 2), mainnet, testnet, another rule-set tag, another Action VK", distinct, &ids.iter().map(|x| h(&x[..4])).collect::<Vec<_>>().join(","));
    m.fact("CR-NET-SAMEPARAMS", "chainref", "the H1C, H1B and chain-A regtest views share one network_id (same genesis and schedule); they differ only by block hash", views["H1C"].network_id == net_a && views["H1B0"].network_id == net_a, "");

    let rk = BuildKeys::research();
    let hk = BuildKeys::from_phrase(H1B_SEED_PHRASE);
    let pol = policy(
        net_a,
        vec![
            entry(&rk.i1, "ZWA-LB-ASSET-X", 0, STATUS_ACTIVE),
            entry(&hk.i1, "H1B-CASE0", 0, STATUS_ACTIVE),
        ],
    );
    let cfg = config(net_a, &pol);
    m.fact("POL-H1C-ASSET", "setup", "the H1B issuer key with description H1B-CASE0 derives the frozen H1C AssetBase ebeb8282…8b16", asset_of(&hk.i1, "H1B-CASE0").to_bytes() == unhex32("ebeb8282ca7dd0728e7fd91e6fef4b369b91548c7d6f67a972ab67502dc68b16"), "");
    m.fact("POL-ASSET-NETINDEP", "setup", "asset X issued on network B has the same AssetBase as on network A (AssetBase does not depend on the network)", c.notes["nx0"]["asset_base"] == c.notes["x0"]["asset_base"], "");
    println!("research policy digest {}", h(&pol.digest()));

    // ================= CR: chain_ref =================
    let r4 = cref(va7, 4);
    let mut t = Tracker::default();
    m.row(
        "CR-01",
        "chainref",
        "honest (net, 4, A4, root4) under A7, Depth(3): depth 3",
        "ACCEPT",
        cr_check(&r4.encode(), Some(va7), &cfg, &mut t),
    );
    let mut x = r4;
    x.block_hash = [0u8; 32];
    x.orchard_root = va7.roots[4].unwrap();
    m.row(
        "CR-02",
        "chainref",
        "height and root right, block hash zero (a height-only reference)",
        "UNKNOWN",
        cr_check(&x.encode(), Some(va7), &cfg, &mut t),
    );
    let mut x = r4;
    x.block_hash = sha256(b"random block hash");
    m.row(
        "CR-03",
        "chainref",
        "random block hash at height 4",
        "UNKNOWN",
        cr_check(&x.encode(), Some(va7), &cfg, &mut t),
    );
    let mut x = r4;
    x.orchard_root = va7.roots[3].unwrap();
    m.row(
        "CR-04",
        "chainref",
        "right block, wrong Orchard root (root after height 3)",
        "TREESTATE_MISMATCH",
        cr_check(&x.encode(), Some(va7), &cfg, &mut t),
    );
    let mut b = r4.encode();
    b[85..117].copy_from_slice(&plus_q(&va7.roots[4].unwrap()));
    m.row(
        "CR-05",
        "chainref",
        "Orchard root encoded as root + q_P (non-canonical)",
        "MALFORMED",
        cr_check(&b, Some(va7), &cfg, &mut t),
    );
    let mut b = r4.encode();
    b[16] = 2;
    m.row(
        "CR-06",
        "chainref",
        "version byte 2",
        "UNSUPPORTED_VERSION",
        cr_check(&b, Some(va7), &cfg, &mut t),
    );
    let mut b = r4.encode();
    b[0] = b'X';
    m.row(
        "CR-07",
        "chainref",
        "wrong domain tag",
        "MALFORMED",
        cr_check(&b, Some(va7), &cfg, &mut t),
    );
    let b = r4.encode();
    m.row(
        "CR-08a",
        "chainref",
        "truncated to 116 bytes",
        "MALFORMED",
        cr_check(&b[..116], Some(va7), &cfg, &mut t),
    );
    let mut b2 = b.clone();
    b2.push(0);
    m.row(
        "CR-08b",
        "chainref",
        "one trailing byte (118 bytes)",
        "MALFORMED",
        cr_check(&b2, Some(va7), &cfg, &mut t),
    );
    let mut x = r4;
    x.height = 0;
    m.row(
        "CR-09",
        "chainref",
        "height 0 (genesis, no Orchard state)",
        "MALFORMED",
        cr_check(&x.encode(), Some(va7), &cfg, &mut t),
    );
    let mut x = r4;
    x.height = 9;
    m.row(
        "CR-10",
        "chainref",
        "future height 9 (tip 7)",
        "UNKNOWN_FUTURE",
        cr_check(&x.encode(), Some(va7), &cfg, &mut t),
    );
    for (id, nid, d) in [
        ("CR-11", net_b, "network B id"),
        ("CR-12a", net_main, "mainnet id"),
        ("CR-12b", net_test, "testnet id"),
        ("CR-13", net_tag, "another rule-set tag"),
        ("CR-14", net_vk, "another Action VK"),
    ] {
        let mut x = r4;
        x.network_id = nid;
        m.row(
            id,
            "chainref",
            &format!("chain-A block and root, {d}"),
            "NETWORK",
            cr_check(&x.encode(), Some(va7), &cfg, &mut t),
        );
    }
    let mut cfg_none = cfg.clone();
    cfg_none.allowed_networks = vec![];
    m.row(
        "CR-15",
        "chainref",
        "verifier allows no network",
        "NETWORK",
        cr_check(&r4.encode(), Some(va7), &cfg_none, &mut t),
    );
    m.row(
        "CR-16",
        "chainref",
        "verifier view unavailable",
        "VIEW_UNAVAILABLE",
        cr_check(&r4.encode(), None, &cfg, &mut t),
    );
    let same_roots = (4..=7).all(|i| va7.roots[i] == va7.roots[4]);
    m.fact(
        "CR-ROOTONLY",
        "chainref",
        "A4…A7 have one Orchard root (empty blocks): a root alone names no height and no block",
        same_roots,
        &h(&va7.roots[4].unwrap()[..8]),
    );
    let mut x = cref(va7, 5);
    x.height = 4;
    m.row(
        "CR-17",
        "chainref",
        "A5's hash with height 4 and the (equal) root",
        "UNKNOWN",
        cr_check(&x.encode(), Some(va7), &cfg, &mut t),
    );
    m.row(
        "CR-18a",
        "chainref",
        "(net, 5, A5) depth 2 under Depth(3)",
        "NOT_FINAL",
        cr_check(&cref(va7, 5).encode(), Some(va7), &cfg, &mut t),
    );
    m.row(
        "CR-18b",
        "chainref",
        "(net, 7, A7) tip under Depth(3)",
        "NOT_FINAL",
        cr_check(&cref(va7, 7).encode(), Some(va7), &cfg, &mut t),
    );
    let hv = &views["H1C"];
    m.row(
        "CR-19",
        "chainref",
        "H1C chain reference (same network_id, other chain instance) under A7",
        "UNKNOWN",
        cr_check(&cref(hv, 2).encode(), Some(va7), &cfg, &mut t),
    );

    // ================= RT: root =================
    let mut t = Tracker::default();
    let rev = |v: &ChainView, note: &H32, rh: u32| root_ev(v, pos(v, note), rh);
    let x0 = c.cmx("x0");
    let ev_x0 = rev(va7, &x0, 4);
    m.row(
        "RT-01",
        "root",
        "authorized issuance x0 (issuer I1, asset X, h1)",
        "ACCEPT",
        root_check(&x0, &ev_x0.encode(), &r4, va7, &cfg, &mut t),
    );
    let z0 = c.cmx("z0");
    m.row(
        "RT-02",
        "root",
        "consensus-valid issuance by unauthorized issuer I2 (z0)",
        "ROOT_POLICY_ISSUER",
        root_check(&z0, &rev(va7, &z0, 4).encode(), &r4, va7, &cfg, &mut t),
    );
    let y0 = c.cmx("y0");
    m.row(
        "RT-03",
        "root",
        "authorized issuer, other asset Y (y0)",
        "ROOT_POLICY_ASSET",
        root_check(&y0, &rev(va7, &y0, 4).encode(), &r4, va7, &cfg, &mut t),
    );
    let vnet = &views["NET"];
    let nx0 = c.cmx("nx0");
    let ev_nx0 = rev(vnet, &nx0, 3);
    m.row(
        "RT-04a",
        "root",
        "network-B issuance nx0 with network-B reference, verifier on network A view",
        "NETWORK",
        root_check(&nx0, &ev_nx0.encode(), &cref(vnet, 3), va7, &cfg, &mut t),
    );
    let mut cfg_nb_pol_a = cfg.clone();
    cfg_nb_pol_a.allowed_networks = vec![net_b];
    m.row(
        "RT-04b",
        "root",
        "network-B issuance under a network-B verifier holding the network-A policy",
        "ROOT_POLICY_NETWORK",
        root_check(
            &nx0,
            &ev_nx0.encode(),
            &cref(vnet, 3),
            vnet,
            &cfg_nb_pol_a,
            &mut t,
        ),
    );
    let pol_nb = policy(
        net_b,
        vec![entry(&rk.i1, "ZWA-LB-ASSET-X", 0, STATUS_ACTIVE)],
    );
    let cfg_nb = config(net_b, &pol_nb);
    m.row(
        "RT-04c",
        "root",
        "control: network-B issuance under a network-B verifier and network-B policy",
        "ACCEPT",
        root_check(
            &nx0,
            &ev_nx0.encode(),
            &cref(vnet, 3),
            vnet,
            &cfg_nb,
            &mut t,
        ),
    );
    let rnd = pallas::Base::from_u128(0x1234_5678_9abc_def0).to_repr();
    m.row(
        "RT-05",
        "root",
        "random commitment with x0's evidence",
        "ROOT_CMX",
        root_check(&rnd, &ev_x0.encode(), &r4, va7, &cfg, &mut t),
    );
    let out1 = c.cmx("out1");
    // A non-issuance leaf with a well-formed issuance claim copied from x0
    // (issuer I1, asset X): the strongest form of the attack.
    let as_issuance = |v: &ChainView, note: &H32, rh: u32| {
        let mut e = rev(v, note, rh);
        e.issuer_ik = ev_x0.issuer_ik;
        e.asset_desc_hash = ev_x0.asset_desc_hash;
        e.asset_base = ev_x0.asset_base;
        e
    };
    m.row(
        "RT-06",
        "root",
        "real Action output out1 (not an issue note) as root, claimed as an I1 / asset-X issuance",
        "ROOT_NOT_ISSUANCE",
        root_check(
            &out1,
            &as_issuance(va7, &out1, 4).encode(),
            &r4,
            va7,
            &cfg,
            &mut t,
        ),
    );
    m.row(
        "RT-06z",
        "root",
        "same, with a zero issuer key in the claim",
        "ROOT_MALFORMED",
        root_check(&out1, &rev(va7, &out1, 4).encode(), &r4, va7, &cfg, &mut t),
    );
    let iss_y_txid = va7.leaf(pos(va7, &y0)).unwrap().txid;
    let mut e = ev_x0.clone();
    e.txid = iss_y_txid;
    m.row(
        "RT-07a",
        "root",
        "x0 claimed in ISS_Y's transaction",
        "ROOT_CONTEXT",
        root_check(&x0, &e.encode(), &r4, va7, &cfg, &mut t),
    );
    let mut e = ev_x0.clone();
    e.note_index += 1;
    m.row(
        "RT-07b",
        "root",
        "x0 claimed at the next note index",
        "ROOT_CONTEXT",
        root_check(&x0, &e.encode(), &r4, va7, &cfg, &mut t),
    );
    let mut e = ev_x0.clone();
    e.height = 2;
    m.row(
        "RT-07c",
        "root",
        "x0 claimed at height 2",
        "ROOT_CONTEXT",
        root_check(&x0, &e.encode(), &r4, va7, &cfg, &mut t),
    );
    let x1 = c.cmx("x1");
    m.row(
        "RT-07d",
        "root",
        "x0 commitment with x1's (real issue note) evidence",
        "ROOT_CMX",
        root_check(&x0, &rev(va7, &x1, 4).encode(), &r4, va7, &cfg, &mut t),
    );
    let mut x = r4;
    x.orchard_root = va7.roots[2].unwrap();
    m.row(
        "RT-08a",
        "root",
        "x0 under a reference with a wrong Orchard root",
        "TREESTATE_MISMATCH",
        root_check(&x0, &ev_x0.encode(), &x, va7, &cfg, &mut t),
    );
    let e = rev(va7, &x0, 2);
    m.row(
        "RT-08b",
        "root",
        "x0 with a Merkle path for the tree after height 2, reference at 4",
        "ROOT_MERKLE",
        root_check(&x0, &e.encode(), &r4, va7, &cfg, &mut t),
    );
    let vb8 = &views["B8"];
    m.row(
        "RT-08c",
        "root",
        "x0 with a branch-B reference (B5) under A7",
        "UNKNOWN",
        root_check(&x0, &ev_x0.encode(), &cref(vb8, 5), va7, &cfg, &mut t),
    );
    let x3a = c.cmx("x3a");
    let r1 = cref(va7, 1);
    let mut cfg_k0 = cfg.clone();
    cfg_k0.finality = FinalityMode::Depth(0);
    m.row(
        "RT-08d",
        "root",
        "x3a (issued at h3) under a reference at height 1",
        "ROOT_OUT_OF_RANGE",
        root_check(&x3a, &rev(va7, &x3a, 3).encode(), &r1, va7, &cfg_k0, &mut t),
    );
    m.row(
        "RT-20",
        "root",
        "authorized second issuance of X (x3a, h3, branch A)",
        "ACCEPT",
        root_check(&x3a, &rev(va7, &x3a, 4).encode(), &r4, va7, &cfg, &mut t),
    );
    let mut tb = t.clone();
    m.row(
        "RT-09a",
        "root",
        "x3a (branch A only) after the reorg to B8, same reference r4 (tracked FINALIZED)",
        "FINALITY_VIOLATION",
        root_check(&x3a, &rev(va7, &x3a, 4).encode(), &r4, vb8, &cfg, &mut tb),
    );
    m.row(
        "RT-09b",
        "root",
        "x3a evidence under B8 with a valid branch-B reference (B5)",
        "REJECT:*",
        root_check(
            &x3a,
            &rev(va7, &x3a, 4).encode(),
            &cref(vb8, 5),
            vb8,
            &cfg,
            &mut t,
        ),
    );
    let x3b = c.cmx("x3b");
    m.row(
        "RT-09c",
        "root",
        "x3b (branch B only) evidence under A7",
        "REJECT:*",
        root_check(&x3b, &rev(vb8, &x3b, 5).encode(), &r4, va7, &cfg, &mut t),
    );
    m.row(
        "RT-09d",
        "root",
        "control: x3b under B8, reference B5",
        "ACCEPT",
        root_check(
            &x3b,
            &rev(vb8, &x3b, 5).encode(),
            &cref(vb8, 5),
            vb8,
            &cfg,
            &mut Tracker::default(),
        ),
    );
    m.row(
        "RT-10a",
        "root",
        "x0 commitment with one bit flipped",
        "REJECT:*",
        root_check(&flip(x0, 0), &ev_x0.encode(), &r4, va7, &cfg, &mut t),
    );
    m.row(
        "RT-10b",
        "root",
        "x0 commitment as x0 + q_P (non-canonical)",
        "ROOT_MALFORMED",
        root_check(&plus_q(&x0), &ev_x0.encode(), &r4, va7, &cfg, &mut t),
    );
    // Reference note of ISS_X: the issue note (0,0).
    let iss_x_ref = va7
        .leaves
        .iter()
        .find(|l| {
            l.txid == va7.leaf(pos(va7, &x0)).unwrap().txid
                && l.kind
                    == LeafKind::IssueNote {
                        action_index: 0,
                        note_index: 0,
                    }
        })
        .unwrap()
        .clone();
    m.row(
        "RT-11",
        "root",
        "the reference note (0,0) of ISS_X (value 0) as root",
        "ROOT_SHAPE",
        root_check(
            &iss_x_ref.cmx,
            &root_ev(va7, iss_x_ref.position, 4).encode(),
            &r4,
            va7,
            &cfg,
            &mut t,
        ),
    );
    let mut e = ev_x0.clone();
    e.asset_base = unhex32(c.notes["y0"]["asset_base"].as_str().unwrap());
    m.row(
        "RT-12a",
        "root",
        "x0 with asset Y claimed",
        "ROOT_ASSET_MISMATCH",
        root_check(&x0, &e.encode(), &r4, va7, &cfg, &mut t),
    );
    let mut e = ev_x0.clone();
    e.issuer_ik = ik_bytes(&rk.i2);
    m.row(
        "RT-12b",
        "root",
        "x0 with issuer I2 claimed",
        "ROOT_ASSET_MISMATCH",
        root_check(&x0, &e.encode(), &r4, va7, &cfg, &mut t),
    );
    let mut e = ev_x0.clone();
    e.auth_path[0] = flip(e.auth_path[0], 0);
    m.row(
        "RT-13a",
        "root",
        "x0 path, level-0 sibling mutated",
        "ROOT_MERKLE",
        root_check(&x0, &e.encode(), &r4, va7, &cfg, &mut t),
    );
    let mut e = ev_x0.clone();
    e.auth_path[5] = plus_q(&e.auth_path[5]);
    m.row(
        "RT-13b",
        "root",
        "x0 path, a sibling as sibling + q_P",
        "ROOT_MALFORMED",
        root_check(&x0, &e.encode(), &r4, va7, &cfg, &mut t),
    );
    let mut e = ev_x0.clone();
    e.position += 1;
    m.row(
        "RT-14",
        "root",
        "x0 evidence with position + 1",
        "REJECT:*",
        root_check(&x0, &e.encode(), &r4, va7, &cfg, &mut t),
    );
    let mut cfg_mut = cfg.clone();
    let mut pm = pol.clone();
    pm.entries
        .push(entry(&rk.i2, "ZWA-LB-ASSET-Z", 0, STATUS_ACTIVE));
    cfg_mut.policy_bytes = Some(pm.encode());
    m.row(
        "RT-15",
        "root",
        "verifier loads a mutated policy (I2 added); digest not accepted",
        "ROOT_POLICY_UNTRUSTED",
        root_check(&z0, &rev(va7, &z0, 4).encode(), &r4, va7, &cfg_mut, &mut t),
    );
    let mut cfg_rev = cfg.clone();
    cfg_rev.revoked_policy_digests = vec![pol.digest()];
    m.row(
        "RT-16",
        "root",
        "policy digest revoked",
        "ROOT_POLICY_REVOKED",
        root_check(&x0, &ev_x0.encode(), &r4, va7, &cfg_rev, &mut t),
    );
    let mut cfg_nopol = cfg.clone();
    cfg_nopol.policy_bytes = None;
    m.row(
        "RT-17",
        "root",
        "no policy loaded",
        "ROOT_POLICY_UNTRUSTED",
        root_check(&x0, &ev_x0.encode(), &r4, va7, &cfg_nopol, &mut t),
    );
    let pol_er = policy(
        net_a,
        vec![entry(&rk.i1, "ZWA-LB-ASSET-X", 0, STATUS_REVOKED)],
    );
    let cfg_er = config(net_a, &pol_er);
    m.row(
        "RT-18",
        "root",
        "policy entry for (I1, X) revoked",
        "ROOT_POLICY_ENTRY_REVOKED",
        root_check(&x0, &ev_x0.encode(), &r4, va7, &cfg_er, &mut t),
    );
    let pol_w = policy(
        net_a,
        vec![entry(&rk.i1, "ZWA-LB-ASSET-X", 2, STATUS_ACTIVE)],
    );
    let cfg_w = config(net_a, &pol_w);
    m.row(
        "RT-19a",
        "root",
        "policy window starts at height 2; x0 issued at 1",
        "ROOT_POLICY_WINDOW",
        root_check(&x0, &ev_x0.encode(), &r4, va7, &cfg_w, &mut t),
    );
    m.row(
        "RT-19b",
        "root",
        "same window; x3a issued at 3",
        "ACCEPT",
        root_check(&x3a, &rev(va7, &x3a, 4).encode(), &r4, va7, &cfg_w, &mut t),
    );
    let pol_noi = policy(net_a, vec![entry(&hk.i1, "H1B-CASE0", 0, STATUS_ACTIVE)]);
    let cfg_noi = config(net_a, &pol_noi);
    m.row(
        "RT-24",
        "root",
        "policy without issuer I1",
        "ROOT_POLICY_ISSUER",
        root_check(&x0, &ev_x0.encode(), &r4, va7, &cfg_noi, &mut t),
    );
    // Checkpoint-trust: frozen H1B CASE2A (corrupted issueAuthSig) committed by checkpoint.
    let v2a = &views["H1B2A"];
    let v0 = &views["H1B0"];
    let issued = |v: &ChainView| {
        v.leaves
            .iter()
            .find(|l| {
                l.kind
                    == LeafKind::IssueNote {
                        action_index: 0,
                        note_index: 1,
                    }
            })
            .unwrap()
            .clone()
    };
    let mut cfg_cp = cfg.clone();
    cfg_cp.finality = FinalityMode::Checkpoint;
    cfg_cp.trusted_checkpoints = vec![(1, v2a.hashes[1])];
    let l2a = issued(v2a);
    m.fact("RT-21-node", "root", "Zebra committed frozen CASE2A (rejected under full validation: InvalidIssueBundleSig) through its checkpoint path", v2a.node_checkpoint_max == 1 && v2a.tip == 1, "");
    m.row(
        "RT-21",
        "root",
        "CASE2A's issued note under the checkpoint view: Layer B re-verifies issueAuthSig",
        "ROOT_ISSUANCE_INVALID",
        root_check(
            &l2a.cmx,
            &root_ev(v2a, l2a.position, 1).encode(),
            &cref(v2a, 1),
            v2a,
            &cfg_cp,
            &mut t,
        ),
    );
    let mut cfg_cp0 = cfg_cp.clone();
    cfg_cp0.trusted_checkpoints = vec![(1, v0.hashes[1])];
    let l0 = issued(v0);
    m.row(
        "RT-21c",
        "root",
        "control: frozen CASE0's issued note, also checkpoint-committed",
        "ACCEPT",
        root_check(
            &l0.cmx,
            &root_ev(v0, l0.position, 1).encode(),
            &cref(v0, 1),
            v0,
            &cfg_cp0,
            &mut t,
        ),
    );
    let h1c_root = unhex32("3d48e416adc19b0f8cae3fb8a794e2e0a0fc9bc3d7b8191a1a869d037b3cba19");
    m.row(
        "RT-22",
        "root",
        "frozen H1C issued note (0,1) under the H1C view, reference h2 (depth 3)",
        "ACCEPT",
        root_check(
            &h1c_root,
            &rev(hv, &h1c_root, 2).encode(),
            &cref(hv, 2),
            hv,
            &cfg,
            &mut t,
        ),
    );
    let fsame_root = c.step_x("fsame0").cmx_old;
    let found = views.values().all(|v| v.find(&fsame_root).is_empty());
    m.fact(
        "RT-23-absent",
        "root",
        "R3-E F-SAME root (synthetic note under the H1C AssetBase) is a leaf of no captured view",
        found,
        "",
    );
    m.row(
        "RT-23",
        "root",
        "R8b: F-SAME synthetic root with the real H1C root's evidence",
        "ROOT_CMX",
        root_check(
            &fsame_root,
            &rev(hv, &h1c_root, 2).encode(),
            &cref(hv, 2),
            hv,
            &cfg,
            &mut t,
        ),
    );
    let e = ev_x0.encode();
    m.row(
        "RT-25a",
        "root",
        "root evidence truncated by one byte",
        "ROOT_MALFORMED",
        root_check(&x0, &e[..e.len() - 1], &r4, va7, &cfg, &mut t),
    );
    let mut e = ev_x0.clone();
    e.issuer_ik[0] = 1;
    m.row(
        "RT-25b",
        "root",
        "issuer key with algorithm byte 0x01",
        "ROOT_MALFORMED",
        root_check(&x0, &e.encode(), &r4, va7, &cfg, &mut t),
    );
    let mut e = ev_x0.clone();
    e.asset_base = orchard::note::AssetBase::zatoshi().to_bytes();
    m.row(
        "RT-25c",
        "root",
        "native (zatoshi) AssetBase claimed",
        "ROOT_MALFORMED",
        root_check(&x0, &e.encode(), &r4, va7, &cfg, &mut t),
    );

    // ================= TM: terminal / MAF =================
    let mut t = Tracker::default();
    let tev = |v: &ChainView, note: &H32, rh: u32| term_ev(v, pos(v, note), rh);
    let out3 = c.cmx("out3");
    let ev_out3 = tev(va7, &out3, 4);
    m.row(
        "TM-01",
        "terminal",
        "real Action output out3 (T3, h4)",
        "ACCEPT",
        term_check(&out3, &ev_out3.encode(), &r4, va7, &cfg, &mut t, &keys),
    );
    let ff = c.step_x("fforeign3").cmx_new;
    let fr = c.step_x("freal3").cmx_new;
    m.fact(
        "TM-02-absent",
        "terminal",
        "F-FOREIGN and F-REAL terminals (circuit-valid, synthetic) are leaves of no captured view",
        views
            .values()
            .all(|v| v.find(&ff).is_empty() && v.find(&fr).is_empty()),
        "",
    );
    m.row(
        "TM-02a",
        "terminal",
        "R9b: F-FOREIGN synthetic terminal with out3's evidence",
        "TERM_CMX",
        term_check(&ff, &ev_out3.encode(), &r4, va7, &cfg, &mut t, &keys),
    );
    m.row(
        "TM-02b",
        "terminal",
        "F-REAL synthetic terminal (real root, synthetic successors) with H1C terminal's evidence",
        "TERM_CMX",
        term_check(
            &fr,
            &tev(
                hv,
                &unhex32("1098249bb88c2cf488e4169f4a03613c01f9411b723efdd76ea182679b044e06"),
                2,
            )
            .encode(),
            &cref(hv, 2),
            hv,
            &cfg,
            &mut t,
            &keys,
        ),
    );
    let out1p = c.cmx("out1p");
    m.row("TM-03a", "terminal", "real Action output of another transaction (out1p, T1P) with its own evidence: a genuine terminal", "ACCEPT", term_check(&out1p, &tev(va7, &out1p, 4).encode(), &r4, va7, &cfg, &mut t, &keys));
    m.row(
        "TM-03b",
        "terminal",
        "out1p with out3's evidence",
        "TERM_CMX",
        term_check(&out1p, &ev_out3.encode(), &r4, va7, &cfg, &mut t, &keys),
    );
    m.row("TM-04", "terminal", "real Action output of another block (out1, h2) with its own evidence: any block ≤ reference", "ACCEPT", term_check(&out1, &tev(va7, &out1, 4).encode(), &r4, va7, &cfg, &mut t, &keys));
    m.row(
        "TM-05a",
        "terminal",
        "out3 with a branch-B reference (B4) under A7",
        "UNKNOWN",
        term_check(
            &out3,
            &ev_out3.encode(),
            &cref(vb8, 4),
            va7,
            &cfg,
            &mut t,
            &keys,
        ),
    );
    m.row(
        "TM-05b",
        "terminal",
        "out3 (h4) with a reference at height 3",
        "TERM_OUT_OF_RANGE",
        term_check(
            &out3,
            &tev(va7, &out3, 4).encode(),
            &cref(va7, 3),
            va7,
            &cfg_k0,
            &mut t,
            &keys,
        ),
    );
    let mut x = r4;
    x.orchard_root = va7.roots[3].unwrap();
    m.row(
        "TM-05c",
        "terminal",
        "out3 with a reference carrying the h3 root",
        "TREESTATE_MISMATCH",
        term_check(&out3, &ev_out3.encode(), &x, va7, &cfg, &mut t, &keys),
    );
    m.row(
        "TM-06a",
        "terminal",
        "out3 commitment with one bit flipped",
        "REJECT:*",
        term_check(
            &flip(out3, 0),
            &ev_out3.encode(),
            &r4,
            va7,
            &cfg,
            &mut t,
            &keys,
        ),
    );
    m.row(
        "TM-06b",
        "terminal",
        "out3 commitment as out3 + q_P (non-canonical)",
        "TERM_MALFORMED",
        term_check(
            &plus_q(&out3),
            &ev_out3.encode(),
            &r4,
            va7,
            &cfg,
            &mut t,
            &keys,
        ),
    );
    for lvl in [0usize, 7, 31] {
        let mut e = ev_out3.clone();
        e.auth_path[lvl] = flip(e.auth_path[lvl], 3);
        m.row(
            &format!("TM-07-L{lvl}"),
            "terminal",
            &format!("out3 path, level-{lvl} sibling mutated"),
            "TERM_MERKLE",
            term_check(&out3, &e.encode(), &r4, va7, &cfg, &mut t, &keys),
        );
    }
    let mut e = ev_out3.clone();
    e.auth_path[0] = plus_q(&e.auth_path[0]);
    m.row("TM-07-NC", "terminal", "MK-NONCANON: a sibling given as sibling + q_P (the Action circuit's alias, MK-15) is not representable", "TERM_MALFORMED", term_check(&out3, &e.encode(), &r4, va7, &cfg, &mut t, &keys));
    for (id, d, p) in [
        ("TM-08a", "position + 1", ev_out3.position + 1),
        ("TM-08b", "position − 1", ev_out3.position - 1),
        (
            "TM-08c",
            "position with bit 31 set",
            ev_out3.position | (1 << 31),
        ),
    ] {
        let mut e = ev_out3.clone();
        e.position = p;
        m.row(
            id,
            "terminal",
            &format!("out3 evidence, {d}"),
            "REJECT:*",
            term_check(&out3, &e.encode(), &r4, va7, &cfg, &mut t, &keys),
        );
    }
    let mut e = ev_out3.clone();
    e.position ^= 1;
    let p_sib = e.position;
    let path_ok = zwa_layer_b::root::path_from(p_sib, &e.auth_path)
        .unwrap()
        .root(ExtractedNoteCommitment::from_bytes(&out3).unwrap())
        .to_bytes()
        == r4.orchard_root;
    m.fact("TM-08d", "terminal", "position with bit 0 flipped: the path no longer reaches the reference root (Action index is not a free field)", !path_ok, "");
    let vnet = &views["NET"];
    let nout1 = c.cmx("nout1");
    let ev_n = tev(vnet, &nout1, 3);
    m.row(
        "TM-10a",
        "terminal",
        "network-B Action output with network-B reference, verifier on network A view",
        "NETWORK",
        term_check(
            &nout1,
            &ev_n.encode(),
            &cref(vnet, 3),
            va7,
            &cfg,
            &mut t,
            &keys,
        ),
    );
    m.row(
        "TM-10b",
        "terminal",
        "network-B Action output under a network-A verifier holding the NET view",
        "NETWORK",
        term_check(
            &nout1,
            &ev_n.encode(),
            &cref(vnet, 3),
            vnet,
            &cfg,
            &mut t,
            &keys,
        ),
    );
    m.row(
        "TM-10c",
        "terminal",
        "control: network-B terminal under a network-B verifier",
        "ACCEPT",
        term_check(
            &nout1,
            &ev_n.encode(),
            &cref(vnet, 3),
            vnet,
            &cfg_nb,
            &mut t,
            &keys,
        ),
    );
    let mut tfin = Tracker::default();
    let _ = term_check(&out3, &ev_out3.encode(), &r4, va7, &cfg, &mut tfin, &keys);
    m.row(
        "TM-11a",
        "terminal",
        "out3 after the reorg (B8), same reference r4 seen FINALIZED under K=3",
        "FINALITY_VIOLATION",
        term_check(&out3, &ev_out3.encode(), &r4, vb8, &cfg, &mut tfin, &keys),
    );
    m.row(
        "TM-11b",
        "terminal",
        "out3 after the reorg, fresh verifier (never saw r4)",
        "UNKNOWN",
        term_check(
            &out3,
            &ev_out3.encode(),
            &r4,
            vb8,
            &cfg,
            &mut Tracker::default(),
            &keys,
        ),
    );
    m.row(
        "TM-11c",
        "terminal",
        "out3's evidence under B8 with a valid B reference: that position holds out3b",
        "TERM_CMX",
        term_check(
            &out3,
            &ev_out3.encode(),
            &cref(vb8, 5),
            vb8,
            &cfg,
            &mut t,
            &keys,
        ),
    );
    let outv = c.cmx("outv");
    m.fact(
        "TM-12-absent",
        "terminal",
        "outv (TV, in rejected block B9R) is a leaf of no captured view",
        views.values().all(|v| v.find(&outv).is_empty()),
        "",
    );
    let mut x = cref(vb8, 8);
    x.height = 9;
    m.row(
        "TM-12a",
        "terminal",
        "outv with a reference at height 9 (the rejected block's height)",
        "UNKNOWN_FUTURE",
        term_check(&outv, &ev_out3.encode(), &x, vb8, &cfg, &mut t, &keys),
    );
    // Prover-side hypothetical tree: B8's leaves plus the rejected block's TV leaves.
    let hyp = |v: &ChainView, extra: &[&str]| -> (u64, H32, [H32; 32]) {
        let mut leaves: Vec<H32> = v.leaves.iter().map(|l| l.cmx).collect();
        for n in extra {
            let tx = zwa_layer_b::util::read_tx(
                &fs::read_to_string(c.fx.join("tx").join(format!("{n}.tx.hex"))).unwrap(),
            );
            leaves.extend(tx_leaves(&tx).into_iter().map(|(_, x)| x));
        }
        let target = c.cmx(if extra[0] == "TV" { "outv" } else { "outu" });
        let p = leaves.iter().position(|l| l == &target).unwrap() as u64;
        let mut tree = zwa_layer_b::view::new_tree();
        for (i, l) in leaves.iter().enumerate() {
            tree.append(orchard::tree::MerkleHashOrchard::from_cmx(
                &ExtractedNoteCommitment::from_bytes(l).unwrap(),
            ));
            if i as u64 == p {
                tree.mark();
            }
        }
        tree.checkpoint(0);
        let auth: Vec<orchard::tree::MerkleHashOrchard> = tree
            .witness(incrementalmerkletree::Position::from(p), 0)
            .unwrap();
        let root = tree.root(0).unwrap().to_bytes();
        (
            p,
            root,
            auth.iter()
                .map(|n| n.to_bytes())
                .collect::<Vec<_>>()
                .try_into()
                .unwrap(),
        )
    };
    let (pv, rootv, pathv) = hyp(vb8, &["TV"]);
    let evv = TerminalEvidence {
        position: pv as u32,
        auth_path: pathv,
    };
    m.row(
        "TM-12b",
        "terminal",
        "outv with a path in the hypothetical tree (B8 + TV), reference B8",
        "TERM_OUT_OF_RANGE",
        term_check(
            &outv,
            &evv.encode(),
            &cref(vb8, 8),
            vb8,
            &cfg_k0,
            &mut t,
            &keys,
        ),
    );
    let mut x = cref(vb8, 8);
    x.orchard_root = rootv;
    m.row("TM-12c", "terminal", "same, with the reference carrying the hypothetical root (path valid against an untrusted root)", "TREESTATE_MISMATCH", term_check(&outv, &evv.encode(), &x, vb8, &cfg_k0, &mut t, &keys));
    let outu = c.cmx("outu");
    let (pu, rootu, pathu) = hyp(va7, &["TU"]);
    let evu = TerminalEvidence {
        position: pu as u32,
        auth_path: pathu,
    };
    m.fact(
        "TM-13-absent",
        "terminal",
        "outu (TU: valid transaction bytes, never mined) is a leaf of no captured view",
        views.values().all(|v| v.find(&outu).is_empty()),
        "",
    );
    m.row(
        "TM-13a",
        "terminal",
        "outu with its hypothetical path, reference A7",
        "TERM_OUT_OF_RANGE",
        term_check(
            &outu,
            &evu.encode(),
            &cref(va7, 7),
            va7,
            &cfg_k0,
            &mut t,
            &keys,
        ),
    );
    let mut x = cref(va7, 7);
    x.orchard_root = rootu;
    m.row(
        "TM-13b",
        "terminal",
        "outu with the reference carrying the hypothetical root",
        "TREESTATE_MISMATCH",
        term_check(&outu, &evu.encode(), &x, va7, &cfg_k0, &mut t, &keys),
    );
    m.row(
        "TM-14",
        "terminal",
        "issue-note leaf x1 (real leaf, not an Action output) as terminal",
        "TERM_NOT_ACTION",
        term_check(
            &x1,
            &tev(va7, &x1, 4).encode(),
            &r4,
            va7,
            &cfg,
            &mut t,
            &keys,
        ),
    );
    let vcpx = &views["CPX"];
    let out1x = c.cmx("out1x");
    let mut cfg_cpx = cfg.clone();
    cfg_cpx.finality = FinalityMode::Checkpoint;
    cfg_cpx.trusted_checkpoints = vec![(2, vcpx.hashes[2])];
    m.fact("TM-15-node", "terminal", "Zebra committed A2X (rejected under full validation: could not validate orchard proof) through its checkpoint path", vcpx.node_checkpoint_max == 2 && !vcpx.find(&out1x).is_empty(), "");
    m.row(
        "TM-15",
        "terminal",
        "out1x from checkpoint-committed A2X: Layer B re-verifies the Action proof",
        "TERM_ACTION_PROOF",
        term_check(
            &out1x,
            &tev(vcpx, &out1x, 2).encode(),
            &cref(vcpx, 2),
            vcpx,
            &cfg_cpx,
            &mut t,
            &keys,
        ),
    );
    let vcpa = &views["CPA"];
    let mut cfg_cpa = cfg.clone();
    cfg_cpa.finality = FinalityMode::Checkpoint;
    cfg_cpa.trusted_checkpoints = vec![(4, vcpa.hashes[4])];
    m.row(
        "TM-15c",
        "terminal",
        "control: out1 from checkpoint-committed A2 (honest)",
        "ACCEPT",
        term_check(
            &out1,
            &tev(vcpa, &out1, 2).encode(),
            &cref(vcpa, 2),
            vcpa,
            &cfg_cpa,
            &mut t,
            &keys,
        ),
    );
    let h1c_term = unhex32("1098249bb88c2cf488e4169f4a03613c01f9411b723efdd76ea182679b044e06");
    m.row(
        "TM-17",
        "terminal",
        "frozen H1C terminal under the H1C view, reference h2",
        "ACCEPT",
        term_check(
            &h1c_term,
            &tev(hv, &h1c_term, 2).encode(),
            &cref(hv, 2),
            hv,
            &cfg,
            &mut t,
            &keys,
        ),
    );
    let e = ev_out3.encode();
    m.row(
        "TM-18",
        "terminal",
        "terminal evidence with one trailing byte",
        "TERM_MALFORMED",
        term_check(
            &out3,
            &[e.clone(), vec![0]].concat(),
            &r4,
            va7,
            &cfg,
            &mut t,
            &keys,
        ),
    );

    // ================= FR: finality, reorg, staleness =================
    let va4 = &views["A4"];
    let mut t = Tracker::default();
    m.row(
        "FR-01",
        "finality",
        "A4 view: r4 at the tip (depth 0) → ACCEPTED",
        "NOT_FINAL",
        cr_check(&cref(va4, 4).encode(), Some(va4), &cfg, &mut t),
    );
    m.row(
        "FR-02",
        "finality",
        "A7 view: r4 depth 3 → FINALIZED",
        "ACCEPT",
        cr_check(&r4.encode(), Some(va7), &cfg, &mut t),
    );
    m.row(
        "FR-03",
        "finality",
        "A7 view: r6 depth 1 → ACCEPTED",
        "NOT_FINAL",
        cr_check(&cref(va7, 6).encode(), Some(va7), &cfg, &mut t),
    );
    m.row(
        "FR-04",
        "finality",
        "A7 view: r7 tip → ACCEPTED",
        "NOT_FINAL",
        cr_check(&cref(va7, 7).encode(), Some(va7), &cfg, &mut t),
    );
    m.row(
        "FR-05",
        "finality",
        "B8 view (natural reorg): r6_A, seen ACCEPTED → INVALIDATED",
        "INVALIDATED",
        cr_check(&cref(va7, 6).encode(), Some(vb8), &cfg, &mut t),
    );
    m.row(
        "FR-06",
        "finality",
        "B8 view: r4_A, seen FINALIZED under K=3 → FINALITY_VIOLATION",
        "FINALITY_VIOLATION",
        cr_check(&r4.encode(), Some(vb8), &cfg, &mut t),
    );
    m.row(
        "FR-07",
        "finality",
        "B8 view: r3_A never seen: same height, other hash → UNKNOWN",
        "UNKNOWN",
        cr_check(&cref(va7, 3).encode(), Some(vb8), &cfg, &mut t),
    );
    m.row(
        "FR-08",
        "finality",
        "B8 view: r2 (common prefix A2) remains FINALIZED",
        "ACCEPT",
        cr_check(&cref(va7, 2).encode(), Some(vb8), &cfg, &mut t),
    );
    m.row(
        "FR-09a",
        "finality",
        "B8 view: r5_B depth 3 → FINALIZED",
        "ACCEPT",
        cr_check(&cref(vb8, 5).encode(), Some(vb8), &cfg, &mut t),
    );
    m.row(
        "FR-09b",
        "finality",
        "B8 view: r6_B depth 2 → ACCEPTED",
        "NOT_FINAL",
        cr_check(&cref(vb8, 6).encode(), Some(vb8), &cfg, &mut t),
    );
    let reorg: Value =
        serde_json::from_str(&fs::read_to_string(run.join("sessions/reorg.json")).unwrap())
            .unwrap();
    let obs: BTreeMap<String, String> = reorg["steps"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|s| s["kind"] == "submit")
        .map(|s| {
            (
                s["label"].as_str().unwrap().to_string(),
                s["observed"].as_str().unwrap().to_string(),
            )
        })
        .collect();
    let natural = obs["B3"] == "SIDE"
        && obs["B6"] == "SIDE"
        && obs["B8"] == "BEST"
        && !reorg.to_string().contains("invalidateblock");
    m.fact(
        "FR-REORG",
        "finality",
        "natural work-based reorg: B3…B6 side chain, B7 tie, B8 best; no invalidateblock",
        natural,
        &format!("B7 {}", obs["B7"]),
    );
    m.fact("FR-ZEBRA-1000", "finality", "Zebra MAX_BLOCK_REORG_HEIGHT = 1000 > research K = 3: the FR-06 reorg is consensus-valid (SOURCE: zebra-chain constants.rs 30)", true, "");
    let mut cfg_c = cfg.clone();
    cfg_c.finality = FinalityMode::Checkpoint;
    cfg_c.trusted_checkpoints = vec![(6, va7.hashes[6])];
    m.row(
        "FR-CP-a",
        "finality",
        "Checkpoint mode, trusted (6, A6): r5 covered → FINALIZED",
        "ACCEPT",
        cr_check(
            &cref(va7, 5).encode(),
            Some(va7),
            &cfg_c,
            &mut Tracker::default(),
        ),
    );
    m.row(
        "FR-CP-UNK",
        "finality",
        "Checkpoint mode, trusted (6, A6): r7 above every checkpoint",
        "NOT_FINAL",
        cr_check(
            &cref(va7, 7).encode(),
            Some(va7),
            &cfg_c,
            &mut Tracker::default(),
        ),
    );
    let mut cfg_cc = cfg_c.clone();
    cfg_cc.trusted_checkpoints = vec![(4, vb8.hashes[4])];
    m.row(
        "FR-CP-CONFLICT",
        "finality",
        "trusted checkpoint (4, B4) contradicted by the A7 view",
        "CHECKPOINT_CONFLICT",
        cr_check(&r4.encode(), Some(va7), &cfg_cc, &mut Tracker::default()),
    );
    let mut cfg_cf = cfg_c.clone();
    cfg_cf.trusted_checkpoints = vec![(20, sha256(b"future checkpoint"))];
    m.row(
        "FR-CP-FUTURE",
        "finality",
        "only trusted checkpoint above the tip: unusable",
        "NOT_FINAL",
        cr_check(&r4.encode(), Some(va7), &cfg_cf, &mut Tracker::default()),
    );
    let mut cfg_h = cfg_c.clone();
    cfg_h.finality = FinalityMode::Hybrid(3);
    m.row(
        "FR-HYB-a",
        "finality",
        "Hybrid(3) + (6, A6): r4 by depth",
        "ACCEPT",
        cr_check(&r4.encode(), Some(va7), &cfg_h, &mut Tracker::default()),
    );
    m.row(
        "FR-HYB-b",
        "finality",
        "Hybrid(3) + (6, A6): r6 by checkpoint",
        "ACCEPT",
        cr_check(
            &cref(va7, 6).encode(),
            Some(va7),
            &cfg_h,
            &mut Tracker::default(),
        ),
    );
    m.row(
        "FR-HYB-c",
        "finality",
        "Hybrid(3) + (6, A6): r7 neither",
        "NOT_FINAL",
        cr_check(
            &cref(va7, 7).encode(),
            Some(va7),
            &cfg_h,
            &mut Tracker::default(),
        ),
    );
    let mut cfg_nock = cfg.clone();
    cfg_nock.accept_checkpointed_history = false;
    m.row("FR-CKPT-a", "finality", "CPA view (heights 1–4 checkpoint-committed by the node), accept_checkpointed_history = false", "CHECKPOINTED_HISTORY", cr_check(&r4.encode(), Some(vcpa), &cfg_nock, &mut Tracker::default()));
    m.row(
        "FR-CKPT-b",
        "finality",
        "CPA view, accept_checkpointed_history = true (CKPT inherited)",
        "ACCEPT",
        cr_check(&r4.encode(), Some(vcpa), &cfg, &mut Tracker::default()),
    );
    m.row(
        "FR-CKPT-c",
        "finality",
        "A7 view (no checkpointed history), accept_checkpointed_history = false",
        "ACCEPT",
        cr_check(&r4.encode(), Some(va7), &cfg_nock, &mut Tracker::default()),
    );
    let mut cfg_age = cfg.clone();
    cfg_age.finality = FinalityMode::Depth(2);
    cfg_age.max_age = Some(3);
    m.row(
        "FR-AGE-a",
        "finality",
        "K=2, max_age=3: recent r5 (age 2)",
        "ACCEPT",
        cr_check(
            &cref(va7, 5).encode(),
            Some(va7),
            &cfg_age,
            &mut Tracker::default(),
        ),
    );
    m.row(
        "FR-AGE-b",
        "finality",
        "K=2, max_age=3: r4 (age 3)",
        "ACCEPT",
        cr_check(&r4.encode(), Some(va7), &cfg_age, &mut Tracker::default()),
    );
    m.row(
        "FR-AGE-c",
        "finality",
        "K=2, max_age=3: old canonical r1 (age 6) expired by policy",
        "EXPIRED",
        cr_check(
            &cref(va7, 1).encode(),
            Some(va7),
            &cfg_age,
            &mut Tracker::default(),
        ),
    );
    m.row(
        "FR-AGE-d",
        "finality",
        "no max_age: old canonical r1 stays acceptable",
        "ACCEPT",
        cr_check(
            &cref(va7, 1).encode(),
            Some(va7),
            &cfg,
            &mut Tracker::default(),
        ),
    );
    m.row(
        "FR-AGE-e",
        "finality",
        "future reference (h 8 on A7)",
        "UNKNOWN_FUTURE",
        cr_check(
            &cref(vb8, 8).encode(),
            Some(va7),
            &cfg,
            &mut Tracker::default(),
        ),
    );

    // ================= CB: combined objects =================
    let obj = |v: &ChainView, rh: u32, root: &H32, term: &H32, steps: &[&str]| LineageObject {
        chain_ref: cref(v, rh).encode(),
        root_cmx: *root,
        terminal_cmx: *term,
        root_evidence: root_ev(v, pos(v, root), rh).encode(),
        terminal_evidence: term_ev(v, pos(v, term), rh).encode(),
        edges: c.edges(steps),
    };
    let mut t = Tracker::default();
    let main_o = obj(va7, 4, &x0, &out3, &["s1", "s2", "s3"]);
    m.row(
        "CB-00",
        "combined",
        "MULTI-STEP: x0 → out1 → out2 → out3 (N = 3, all on chain A), reference r4 under A7",
        "ACCEPT",
        obj_check(&main_o, Some(va7), &cfg, &mut t, &keys),
    );
    let h1c_o = obj(hv, 2, &h1c_root, &h1c_term, &["h1c"]);
    m.row(
        "CB-00b",
        "combined",
        "frozen H1C step (N = 1) under the H1C view, reference h2",
        "ACCEPT",
        obj_check(&h1c_o, Some(hv), &cfg, &mut t, &keys),
    );
    let b_o = obj(vb8, 5, &x0, &c.cmx("out3b"), &["s1", "s2b", "s3b"]);
    m.row(
        "CB-00c",
        "combined",
        "branch-B lineage x0 → out1 → out2b → out3b under B8, reference B5",
        "ACCEPT",
        obj_check(&b_o, Some(vb8), &cfg, &mut Tracker::default(), &keys),
    );
    let short_o = obj(va7, 4, &x0, &c.cmx("out2"), &["s1", "s2"]);
    m.row(
        "CB-00d",
        "combined",
        "genuine shorter lineage x0 → out2 (N = 2) to an earlier real output",
        "ACCEPT",
        obj_check(&short_o, Some(va7), &cfg, &mut t, &keys),
    );
    let zout1 = c.cmx("zout1");
    m.row(
        "CB-01",
        "combined",
        "valid lineage z0 → zout1 from a consensus-valid unauthorized issuance (I2)",
        "ROOT_POLICY_ISSUER",
        obj_check(
            &obj(va7, 4, &z0, &zout1, &["sz"]),
            Some(va7),
            &cfg,
            &mut t,
            &keys,
        ),
    );
    let mut o = h1c_o.clone();
    o.root_cmx = fsame_root;
    o.terminal_cmx = c.step_x("fsame3").cmx_new;
    o.edges = c.edges(&["fsame0", "fsame1", "fsame2", "fsame3"]);
    m.row(
        "CB-01b",
        "combined",
        "R8b exact: R3-E F-SAME (valid N = 4, synthetic root under the H1C AssetBase)",
        "ROOT_CMX",
        obj_check(&o, Some(hv), &cfg, &mut t, &keys),
    );
    let mut o = h1c_o.clone();
    o.terminal_cmx = fr;
    o.edges = c.edges(&["freal0", "freal1", "freal2", "freal3"]);
    m.row(
        "CB-02a",
        "combined",
        "real root, valid N = 4 lineage, synthetic terminal (R3-E F-REAL)",
        "TERM_CMX",
        obj_check(&o, Some(hv), &cfg, &mut t, &keys),
    );
    let mut o = h1c_o.clone();
    o.root_cmx = c.step_x("fforeign0").cmx_old;
    o.terminal_cmx = ff;
    o.edges = c.edges(&["fforeign0", "fforeign1", "fforeign2", "fforeign3"]);
    m.row(
        "CB-02b",
        "combined",
        "R9b exact: R3-E F-FOREIGN (valid N = 4, wholly synthetic)",
        "ROOT_CMX",
        obj_check(&o, Some(hv), &cfg, &mut t, &keys),
    );
    let mut o = main_o.clone();
    o.chain_ref = cref(vb8, 5).encode();
    m.row(
        "CB-03a",
        "combined",
        "correct lineage, chain_ref from branch B",
        "UNKNOWN",
        obj_check(&o, Some(va7), &cfg, &mut t, &keys),
    );
    let mut o = main_o.clone();
    let mut x = r4;
    x.orchard_root = va7.roots[3].unwrap();
    o.chain_ref = x.encode();
    m.row(
        "CB-03b",
        "combined",
        "correct lineage, chain_ref with a wrong Orchard root",
        "TREESTATE_MISMATCH",
        obj_check(&o, Some(va7), &cfg, &mut t, &keys),
    );
    let o7 = obj(va7, 7, &x0, &out3, &["s1", "s2", "s3"]);
    m.row(
        "CB-03c",
        "combined",
        "correct lineage, chain_ref at the tip (not final)",
        "NOT_FINAL",
        obj_check(&o7, Some(va7), &cfg, &mut t, &keys),
    );
    let mut o = main_o.clone();
    o.terminal_cmx = nout1;
    o.terminal_evidence = ev_n.encode();
    m.row(
        "CB-04",
        "combined",
        "correct root and lineage proofs, foreign (network-B) terminal",
        "DA_TERMINAL",
        obj_check(&o, Some(va7), &cfg, &mut t, &keys),
    );
    let mut o = main_o.clone();
    o.root_cmx = nx0;
    o.root_evidence = ev_nx0.encode();
    m.row(
        "CB-05",
        "combined",
        "foreign (network-B) root, correct terminal",
        "REJECT:*",
        obj_check(&o, Some(va7), &cfg, &mut t, &keys),
    );
    let mut cfg_stale = cfg.clone();
    cfg_stale.max_age = Some(1);
    m.row(
        "CB-06",
        "combined",
        "correct endpoints, reference r4 older than max_age = 1",
        "EXPIRED",
        obj_check(
            &main_o,
            Some(va7),
            &cfg_stale,
            &mut Tracker::default(),
            &keys,
        ),
    );
    let mut tr = Tracker::default();
    let _ = obj_check(&main_o, Some(va7), &cfg, &mut tr, &keys);
    m.row(
        "CB-07a",
        "combined",
        "correct endpoints, reference reorged after it was FINALIZED (B8 view)",
        "FINALITY_VIOLATION",
        obj_check(&main_o, Some(vb8), &cfg, &mut tr, &keys),
    );
    let mut tr = Tracker::default();
    let o4 = obj(va4, 4, &x0, &out3, &["s1", "s2", "s3"]);
    m.row(
        "CB-07b",
        "combined",
        "same object under A4 (reference at tip, ACCEPTED)",
        "NOT_FINAL",
        obj_check(&o4, Some(va4), &cfg, &mut tr, &keys),
    );
    m.row(
        "CB-07c",
        "combined",
        "then under B8: ACCEPTED reference reorged",
        "INVALIDATED",
        obj_check(&o4, Some(vb8), &cfg, &mut tr, &keys),
    );
    let mut o = main_o.clone();
    let mut x = r4;
    x.network_id = net_b;
    o.chain_ref = x.encode();
    m.row(
        "CB-08",
        "combined",
        "reference carrying network B's id",
        "NETWORK",
        obj_check(&o, Some(va7), &cfg, &mut t, &keys),
    );
    let mut o = main_o.clone();
    let out3b = c.cmx("out3b");
    o.terminal_cmx = out3b;
    o.terminal_evidence = term_ev(vb8, pos(vb8, &out3b), 5).encode();
    o.edges = c.edges(&["s1", "s2b", "s3b"]);
    m.row(
        "CB-09a",
        "combined",
        "root on accepted chain A + real branch-B terminal and lineage, reference r4_A",
        "TERM_CMX",
        obj_check(&o, Some(va7), &cfg, &mut t, &keys),
    );
    let mut o = main_o.clone();
    o.root_cmx = x3b;
    o.root_evidence = root_ev(vb8, pos(vb8, &x3b), 5).encode();
    m.row(
        "CB-09b",
        "combined",
        "root from fork B (x3b) + terminal on accepted chain A",
        "REJECT:*",
        obj_check(&o, Some(va7), &cfg, &mut t, &keys),
    );
    let mut o = main_o.clone();
    o.root_evidence = root_ev(hv, pos(hv, &h1c_root), 2).encode();
    o.root_cmx = h1c_root;
    m.row(
        "CB-09c",
        "combined",
        "root authenticated against the H1C chain (another chain instance) + chain-A terminal",
        "REJECT:*",
        obj_check(&o, Some(va7), &cfg, &mut t, &keys),
    );
    let enc = main_o.encode();
    let dual = [
        &enc[..17 + 117],
        &cref(vb8, 5).encode()[..],
        &enc[17 + 117..],
    ]
    .concat();
    m.row(
        "CB-09d",
        "combined",
        "a second chain_ref spliced into the wire format",
        "MALFORMED",
        raw_obj_check(&dual, Some(va7), &cfg, &mut t, &keys).map(|_| String::new()),
    );
    m.row(
        "CB-10",
        "combined",
        "verifier holds a mutated policy",
        "ROOT_POLICY_UNTRUSTED",
        obj_check(&main_o, Some(va7), &cfg_mut, &mut t, &keys),
    );
    m.row(
        "CB-11a",
        "combined",
        "issuer entry revoked",
        "ROOT_POLICY_ENTRY_REVOKED",
        obj_check(&main_o, Some(va7), &cfg_er, &mut t, &keys),
    );
    m.row(
        "CB-11b",
        "combined",
        "issuer unknown to the policy",
        "ROOT_POLICY_ISSUER",
        obj_check(&main_o, Some(va7), &cfg_noi, &mut t, &keys),
    );
    let mut tr = Tracker::default();
    m.row(
        "CB-12a",
        "combined",
        "replay: the same accepted object again, same view",
        "ACCEPT",
        obj_check(&main_o, Some(va7), &cfg, &mut tr, &keys),
    );
    m.row(
        "CB-12b",
        "combined",
        "replay: same object, same view, second time",
        "ACCEPT",
        obj_check(&main_o, Some(va7), &cfg, &mut tr, &keys),
    );
    m.row("CB-12c", "combined", "replay against another state of the same chain (CPA: checkpoint-committed node, CKPT accepted)", "ACCEPT", obj_check(&main_o, Some(vcpa), &cfg, &mut Tracker::default(), &keys));
    m.row(
        "CB-12d",
        "combined",
        "replay against CPA with checkpointed history not accepted",
        "CHECKPOINTED_HISTORY",
        obj_check(
            &main_o,
            Some(vcpa),
            &cfg_nock,
            &mut Tracker::default(),
            &keys,
        ),
    );
    m.row(
        "CB-12e",
        "combined",
        "replay against the H1C chain (same network_id, other blocks)",
        "UNKNOWN",
        obj_check(&main_o, Some(hv), &cfg, &mut Tracker::default(), &keys),
    );
    let mut o = main_o.clone();
    o.edges = c.edges(&["s1", "s3"]);
    m.row(
        "CB-13a",
        "combined",
        "truncated lineage with genuine endpoints: [s1, s3]",
        "DA_JOIN(0)",
        obj_check(&o, Some(va7), &cfg, &mut t, &keys),
    );
    let mut o = main_o.clone();
    o.edges = c.edges(&["s2", "s3"]);
    m.row(
        "CB-13b",
        "combined",
        "truncated: [s2, s3] with root x0",
        "DA_ROOT",
        obj_check(&o, Some(va7), &cfg, &mut t, &keys),
    );
    let mut sub = obj(va7, 4, &out1, &out3, &["s2", "s3"]);
    sub.root_evidence = as_issuance(va7, &out1, 4).encode();
    m.row(
        "CB-13c",
        "combined",
        "genuine on-chain sub-path out1 → out3 with a non-issuance root",
        "ROOT_NOT_ISSUANCE",
        obj_check(&sub, Some(va7), &cfg, &mut t, &keys),
    );
    let mut o = main_o.clone();
    o.edges = c.edges(&["s1", "s2b", "s3b"]);
    m.row(
        "CB-14a",
        "combined",
        "Design-A proof set of another rooted path (branch B) with terminal out3",
        "DA_TERMINAL",
        obj_check(&o, Some(va7), &cfg, &mut t, &keys),
    );
    let mut o = main_o.clone();
    o.edges = c.edges(&["sz"]);
    m.row(
        "CB-14b",
        "combined",
        "Design-A proof set of the Z lineage with root x0 / terminal out3",
        "DA_ROOT",
        obj_check(&o, Some(va7), &cfg, &mut t, &keys),
    );
    let enc = main_o.encode();
    m.row(
        "CB-15a",
        "combined",
        "one trailing byte",
        "MALFORMED",
        raw_obj_check(
            &[enc.clone(), vec![0]].concat(),
            Some(va7),
            &cfg,
            &mut t,
            &keys,
        )
        .map(|_| String::new()),
    );
    m.row(
        "CB-15b",
        "combined",
        "truncated by one byte",
        "MALFORMED",
        raw_obj_check(&enc[..enc.len() - 1], Some(va7), &cfg, &mut t, &keys).map(|_| String::new()),
    );
    let n_at = 17 + 117 + 64 + 1169 + 1028;
    let mut b = enc.clone();
    b[n_at..n_at + 2].copy_from_slice(&0u16.to_le_bytes());
    m.row(
        "CB-15c",
        "combined",
        "N = 0",
        "MALFORMED",
        raw_obj_check(&b[..n_at + 2], Some(va7), &cfg, &mut t, &keys).map(|_| String::new()),
    );
    let mut b = enc.clone();
    b[n_at..n_at + 2].copy_from_slice(&1025u16.to_le_bytes());
    m.row(
        "CB-15d",
        "combined",
        "N = 1025",
        "MALFORMED",
        raw_obj_check(&b, Some(va7), &cfg, &mut t, &keys).map(|_| String::new()),
    );
    let mut b = enc.clone();
    b[n_at + 2 + 96..n_at + 2 + 98].copy_from_slice(&0u16.to_le_bytes());
    m.row(
        "CB-15e",
        "combined",
        "first proof length 0",
        "MALFORMED",
        raw_obj_check(&b, Some(va7), &cfg, &mut t, &keys).map(|_| String::new()),
    );
    let mut b = enc.clone();
    b[0] = b'z';
    m.row(
        "CB-15f",
        "combined",
        "wrong object tag",
        "MALFORMED",
        raw_obj_check(&b, Some(va7), &cfg, &mut t, &keys).map(|_| String::new()),
    );
    let mut b = enc.clone();
    b[16] = 2;
    m.row(
        "CB-15g",
        "combined",
        "object version 2",
        "MALFORMED",
        raw_obj_check(&b, Some(va7), &cfg, &mut t, &keys).map(|_| String::new()),
    );
    let mut b = enc.clone();
    b[17 + 16] = 2;
    m.row(
        "CB-15h",
        "combined",
        "embedded chain_ref version 2",
        "UNSUPPORTED_VERSION",
        raw_obj_check(&b, Some(va7), &cfg, &mut t, &keys).map(|_| String::new()),
    );
    let mut o = main_o.clone();
    o.edges[0].x.cmx_old = plus_q(&o.edges[0].x.cmx_old);
    m.row(
        "CB-15i",
        "combined",
        "first step's cmx_old as cmx_old + q_P",
        "DA_NONCANONICAL(0)",
        obj_check(&o, Some(va7), &cfg, &mut t, &keys),
    );
    let mut o = main_o.clone();
    o.root_cmx = plus_q(&x0);
    m.row(
        "CB-15j",
        "combined",
        "root_cmx as x0 + q_P",
        "ROOT_MALFORMED",
        obj_check(&o, Some(va7), &cfg, &mut t, &keys),
    );
    let mut o = main_o.clone();
    o.edges[1].proof[100] ^= 1;
    m.row(
        "CB-15k",
        "combined",
        "second proof with one flipped byte",
        "DA_PROOF(1)",
        obj_check(&o, Some(va7), &cfg, &mut t, &keys),
    );
    m.row(
        "CB-15l",
        "combined",
        "verifier view unavailable",
        "VIEW_UNAVAILABLE",
        obj_check(&main_o, None, &cfg, &mut t, &keys),
    );
    let n_o = obj(vnet, 3, &nx0, &nout1, &["sn"]);
    m.row(
        "CB-16a",
        "combined",
        "network-B lineage under its own verifier and policy",
        "ACCEPT",
        obj_check(&n_o, Some(vnet), &cfg_nb, &mut Tracker::default(), &keys),
    );
    m.row(
        "CB-16b",
        "combined",
        "network-B lineage under the network-A verifier",
        "NETWORK",
        obj_check(&n_o, Some(va7), &cfg, &mut Tracker::default(), &keys),
    );
    let cpx_o = obj(vcpx, 2, &x0, &out1x, &["s1x"]);
    m.row(
        "CB-17a",
        "combined",
        "x0 → out1x from checkpoint-committed A2X (invalid Action proof); Design A valid",
        "TERM_ACTION_PROOF",
        obj_check(&cpx_o, Some(vcpx), &cfg_cpx, &mut Tracker::default(), &keys),
    );
    let cpa_o = obj(vcpa, 4, &x0, &out3, &["s1", "s2", "s3"]);
    m.row(
        "CB-17b",
        "combined",
        "x0 → out3 on checkpoint-committed chain A (CPA), CKPT accepted",
        "ACCEPT",
        obj_check(&cpa_o, Some(vcpa), &cfg_cpa, &mut Tracker::default(), &keys),
    );
    let all_ordered = m
        .rows
        .iter()
        .filter(|r| r["group"] == "combined" && r["actual"] == "ACCEPT")
        .all(|r| {
            let d = r["detail"].as_str().unwrap();
            let num = |k: &str| {
                d.split(k)
                    .nth(1)
                    .and_then(|s| s.split_whitespace().next())
                    .and_then(|s| s.parse::<u32>().ok())
                    .unwrap()
            };
            num("root h=") < num("terminal h=")
        });
    m.fact("CB-18", "combined", "in every accepted object the root height is strictly below the terminal height (CONTEXT is unreachable for honest data: C2)", all_ordered, "");

    // ================= MS: multi-step on-chain lineage (H1-ROOTED items on real chains) =================
    // For each accepted lineage, check directly on the verifier's view what
    // H1-ROOTED items 1-5 assert: each step i is one on-chain Action that both
    // reveals nf_old_i and creates cmx_new_i; the first cmx_old is the root issue
    // note; heights strictly increase; anchors are earlier roots; no repeated
    // cmx or nf; each revealed nf appears once in the chain (C3).
    let lineages: [(&str, &ChainView, &[&str], H32); 4] = [
        ("MS-A", va7, &["s1", "s2", "s3"], x0),
        ("MS-B", vb8, &["s1", "s2b", "s3b"], x0),
        ("MS-H1C", hv, &["h1c"], h1c_root),
        ("MS-NET", vnet, &["sn"], nx0),
    ];
    for (id, v, steps, root) in lineages {
        let mut ok = true;
        let mut why = vec![];
        let first = c.step_x(steps[0]).cmx_old;
        let rl = v.leaf(pos(v, &first)).unwrap();
        ok &= first == root && matches!(rl.kind, LeafKind::IssueNote { .. });
        let mut prev_h = rl.height;
        let mut seen_nf = std::collections::BTreeSet::new();
        let mut seen_cmx = std::collections::BTreeSet::from([first]);
        for (i, s) in steps.iter().enumerate() {
            let xi = c.step_x(s);
            let nl = v.leaf(pos(v, &xi.cmx_new)).unwrap();
            let tx = v.tx(nl.height, nl.tx_index);
            let b = match tx.orchard_bundle() {
                Some(zcash_primitives::transaction::OrchardBundle::OrchardZSA(b)) => b,
                _ => panic!("ZSA"),
            };
            let LeafKind::Action { action_index } = nl.kind else {
                panic!("action")
            };
            let a = &b.actions()[action_index as usize];
            let same_action =
                a.nullifier().to_bytes() == xi.nf_old && a.cmx().to_bytes() == xi.cmx_new;
            let anchor = b.anchor().to_bytes();
            let anchor_ok = (0..nl.height).any(|hh| v.roots[hh as usize] == Some(anchor));
            let nf_count = v
                .blocks
                .iter()
                .flatten()
                .filter_map(|t| match t.orchard_bundle() {
                    Some(zcash_primitives::transaction::OrchardBundle::OrchardZSA(b)) => Some(
                        b.actions()
                            .iter()
                            .filter(|a| a.nullifier().to_bytes() == xi.nf_old)
                            .count(),
                    ),
                    _ => None,
                })
                .sum::<usize>();
            let joined = i == 0 || c.step_x(steps[i - 1]).cmx_new == xi.cmx_old;
            let step_ok = same_action
                && anchor_ok
                && nl.height > prev_h
                && nf_count == 1
                && joined
                && seen_nf.insert(xi.nf_old)
                && seen_cmx.insert(xi.cmx_new);
            why.push(format!("step {s}: h{}→h{} action {action_index} same-action {same_action} anchor-earlier {anchor_ok} nf-once {}", prev_h, nl.height, nf_count == 1));
            ok &= step_ok;
            prev_h = nl.height;
        }
        m.fact(id, "multistep", &format!("{} on-chain: every step is one Action revealing nf_old and creating cmx_new; root is an issue note; heights increase; anchors earlier; nf and cmx unique", steps.join(" → ")), ok, &why.join("; "));
    }

    // ================= Output =================
    let total = m.rows.len();
    println!("\n{total} rows, {} PASS, {} FAIL", total - m.fails, m.fails);
    let mut jl = String::new();
    let mut txt = String::new();
    for r in &m.rows {
        jl.push_str(&serde_json::to_string(r).unwrap());
        jl.push('\n');
        txt.push_str(&format!(
            "[{}] {} | {} | expected {} | actual {} | {}\n",
            r["verdict"].as_str().unwrap(),
            r["id"].as_str().unwrap(),
            r["vector"].as_str().unwrap(),
            r["expected"].as_str().unwrap(),
            r["actual"].as_str().unwrap(),
            r["detail"].as_str().unwrap()
        ));
    }
    txt.push_str(&format!(
        "TOTAL {total} PASS {} FAIL {}\n",
        total - m.fails,
        m.fails
    ));
    if let Ok(dir) = std::env::var("LAYER_B_EVIDENCE_DIR") {
        fs::create_dir_all(&dir).unwrap();
        fs::write(Path::new(&dir).join("layer-b-core-replay.jsonl"), jl).unwrap();
    }
    let _ = txt;
    assert_eq!(m.fails, 0, "frozen research matrix changed");
}
