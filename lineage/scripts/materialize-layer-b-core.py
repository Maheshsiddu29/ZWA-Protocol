#!/usr/bin/env python3
"""Deterministically extract the frozen Layer-B verifier without RPC tooling.

The seven source files are read from the pinned sibling research checkout only
when generating or comparing. Normal Cargo builds use the checked-in output.
No verification function body is edited by this script.
"""

import argparse
import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parents[1]
RESEARCH = HERE.parents[1] / "ZWA-Lineage-Research"
SOURCE = RESEARCH / "post-h1f/layer-b/experiments/src"
DEST = HERE / "vendor/layer-b-core/src"
MANIFEST = HERE / "vendor/layer-b-core/SOURCE_MANIFEST.json"
PIN = "3e7115d0e4c2f1cf36355f028447fc1b58290583"
FILES = ("chainref.rs", "finality.rs", "policy.rs", "root.rs", "terminal.rs", "util.rs", "view.rs")


def between(s: str, start: str, end: str) -> str:
    assert s.count(start) == 1, start
    assert s.count(end) == 1, end
    a = s.index(start)
    b = s.index(end, a)
    return s[a:b]


def through_end(s: str, start: str) -> str:
    assert s.count(start) == 1, start
    return s[s.index(start) :]


def extract(name: str, s: str) -> str:
    if name in ("chainref.rs", "finality.rs", "policy.rs"):
        return s
    if name in ("root.rs", "terminal.rs"):
        verifier = s[: s.index("/// Prover-side helper:")]
        encoder = between(verifier, "    pub fn encode(&self) -> Vec<u8> {", "    pub fn decode(")
        return verifier.replace(encoder, "").rstrip() + "\n"
    if name == "view.rs":
        intro = s[: s.index("impl ChainView {")]
        intro = intro.replace("use incrementalmerkletree::Position;\n", "")
        intro = intro.replace("use orchard::tree::{MerkleHashOrchard, MerklePath};", "use orchard::tree::MerkleHashOrchard;")
        intro = intro.replace("use serde_json::{json, Value};", "use serde_json::Value;")
        intro = intro.replace("parse_block, rpc, rpc_call, sha256_hex", "parse_block, sha256_hex")
        loader = between(s, "    /// Validates a snapshot", "    /// Prover-side helper:")
        return intro + "impl ChainView {\n" + loader + "}\n"
    assert name == "util.rs"
    imports = """//! Frozen Layer-B verification utilities, extracted without RPC and block assembly.

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

"""
    hashes = between(s, "pub type H32", "// ---- JSON-RPC")
    parse = between(s, "/// Parsed block:", "const AUTH_COMMITMENT_PLACEHOLDER")
    merkle = between(s, "/// Bitcoin-style transaction Merkle root", "fn auth_root")
    sighash = through_end(s, "// ---- Shielded sighash")
    return imports + hashes + parse + merkle + sighash


def digest(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    import subprocess
    head = subprocess.check_output(["git", "-C", str(RESEARCH), "rev-parse", "HEAD"], text=True).strip()
    if head != PIN:
        raise SystemExit(f"research HEAD changed: {head}")
    prior = json.loads(MANIFEST.read_text()) if args.check else None
    rows = []
    for name in FILES:
        raw = (SOURCE / name).read_bytes()
        generated = extract(name, raw.decode()).encode()
        target = DEST / name
        row = {
            "research_path": str((SOURCE / name).relative_to(RESEARCH)),
            "production_path": str(target.relative_to(HERE)),
            "research_sha256": digest(raw),
            "production_sha256": digest(generated),
            "byte_identical": raw == generated,
            "extraction": "whole file" if raw == generated else "deterministic exclusion of research-only functions; retained verifier function text unchanged",
        }
        if args.check:
            assert target.read_bytes() == generated, f"generated source differs: {name}"
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(generated)
        rows.append(row)
    manifest = {"research_commit": PIN, "files": rows}
    if args.check:
        assert prior == manifest, "source manifest differs"
    else:
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Layer-B core source {'verified' if args.check else 'materialized'}: {len(rows)} files")


if __name__ == "__main__":
    main()
