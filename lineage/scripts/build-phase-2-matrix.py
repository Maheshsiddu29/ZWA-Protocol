#!/usr/bin/env python3
"""Account for every frozen Layer-B and H1-rooted key row.

The test replay file is produced by full_layer_b_core.rs. This script rejects
missing IDs, changes to frozen outcomes, or any production acceptance of a
frozen rejection. It does not classify research facts as verifier calls.
"""

import collections
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
RESEARCH = HERE.parents[1] / "ZWA-Lineage-Research"
OUT = HERE / "evidence/phase-2/layer-b-case-manifest.json"
GROUPS = ("chainref_vectors", "view_vectors", "root_vectors", "terminal_vectors", "combined_vectors", "multistep_vectors")
HEAD = "3e7115d0e4c2f1cf36355f028447fc1b58290583"

REASONS = {
    "CR-NETIDS": "Research setup compares six network identities; production pins one regtest network and this is not a chain-reference verification request.",
    "CR-NET-SAMEPARAMS": "Compares H1C and H1B chain views that are not production-pinned snapshots; no individual chain-reference result is asserted.",
    "CR-ROOTONLY": "Observation that several A-chain heights share an Orchard root; no chain-reference validation call or result exists for this row.",
    "FR-REORG": "Natural work-based Zebra branch-selection fact, not a production finality-verifier request; its accepted and finalized consequences are replayed in FR-05/06.",
    "FR-ZEBRA-1000": "Zebra consensus reorg-limit source fact, not a production finality-verifier request; production K=3 policy is tested separately.",
    "RT-21-node": "Zebra checkpoint admission fact for CASE2A; production consumes authenticated snapshots and does not run a node.",
    "RT-23-absent": "Synthetic F-SAME root is absent from H1C, which is not a production-pinned snapshot; this row is a fixture-existence fact.",
    "TM-02-absent": "Synthetic F-FOREIGN and F-REAL terminals are absent from captured views; this is a fixture-existence fact, not a terminal authentication call.",
    "TM-08d": "Merkle-path bit-flip observation without a terminal authentication call; production's terminal proof is exercised by TM-08a/b/c and TM-07 variants.",
    "TM-12-absent": "The rejected B9R block is not a production-pinned view; this is a node/fixture absence fact.",
    "TM-13-absent": "TU was never mined and has no authenticated production snapshot leaf; this is a fixture absence fact.",
    "TM-15-node": "Zebra checkpoint admission fact for A2X; production consumes authenticated snapshots and does not run a node.",
    "CB-18": "Universal observation over research accepted objects, not a separately encoded production request; every combined decision is mirrored through the core check.",
    "VW-B9R": "B8R is not a production-pinned snapshot; the row records a node remaining unchanged after a rejected block.",
    "VW-EMPTY-ROOT": "The NET view is not a production-pinned snapshot; the row records its empty-tree root.",
    "VW-H1C-FROZEN": "H1C is not a production-pinned snapshot; the row compares old frozen block hashes and roots.",
    "VW-CHAIN-NAME": "Node-reported RPC chain name is capture metadata; the production verifier performs no RPC query.",
    "VW-3IMPL": "Cross-implementation node/bridgetree/MerklePath witness comparison is a research fact; the production core omits witness generation.",
}
for suffix in ("A", "B", "H1C", "NET"):
    REASONS[f"MS-{suffix}"] = "On-chain ground-truth fact about Action spends, anchors and uniqueness; the production API authenticates endpoints and Design-A proofs, not this investigative predicate."

ROOTED = {
    "RP-LB-VIEWS": ("TRUE", "AGREES", ""),
    "RP-LB-MATRIX": ("TRUE", "AGREES", ""),
    "RP-OBJ-FV": ("ACCEPT", "AGREES", "N=3 A7 production result VERIFIED; normalized to research ACCEPT."),
    "RP-OBJ-CP": ("ACCEPT", "AGREES", "N=3 CPA explicit-trust production result VERIFIED; normalized to research ACCEPT."),
    "CK-OBJ-CP": ("CHECKPOINT_TRUST_REQUIRED", "INTENTIONAL_STRICTER_REJECTION", "Research CP accepts under explicit trust. Production default forbids CKN checkpoint inheritance, and its configured trust constructor rejects CKN; trade remains denied."),
    "CK-OBJ-FV": ("CHECKPOINTED_HISTORY", "AGREES", "Production CKN result CHECKPOINT_TRUST_REQUIRED carries CHECKPOINTED_HISTORY."),
}
ROOTED_REASONS = {
    "CK-OBJ-FVNODE": "Full-validation node view with M2 rejected has tip 1; production pins only the CKN checkpoint view, not the separate FV-node view.",
    "CK-GT-X-OFFCHAIN": "Historical ground-truth leaf absence, not a verifier output; no production API for this investigative predicate.",
    "CK-GT-NF1-UNREVEALED": "Historical ground-truth nullifier absence, not a verifier output; no production API for this investigative predicate.",
    "CK-GT-PROOF-INVALID": "Historical intermediate Action proof fact under CKN, not a production endpoint verifier output.",
    "CK-OLD-M2-C2": "Historical checkpointed M2 anchor observation, not a verifier output; production does not expose the node's checkpoint internals.",
    "CK-OLD-C3": "Historical CKN nullifier uniqueness observation, not a verifier output; production does not expose this investigative predicate.",
    "AO-FAKE-0": "T7FAKE checkpoint view is not a production-pinned snapshot; terminal anchor behavior remains in byte-identical imported terminal source.",
    "AO-SELF-0": "T7SELF checkpoint view is not a production-pinned snapshot; terminal anchor behavior remains in byte-identical imported terminal source.",
    "AO-BRANCH-0": "T7BR checkpoint view is not a production-pinned snapshot; terminal anchor behavior remains in byte-identical imported terminal source.",
    "AO-CTL-0": "T7CTL control view is not a production-pinned snapshot; no production v3 fixture exists for its terminal.",
    "C5-CP-NOT-COMMITTED": "The invalid issued-rho CASE1 block never became a committed snapshot; production cannot authenticate it as a chain view.",
    "C5-CP-REASON": "Zebra finalized-state writer fail-stop observation, not a production root-authentication request.",
}
ROOTED_TESTS = {
    "RP-LB-VIEWS": "tests/phase2.rs::snapshots_hash_and_rederive",
    "RP-LB-MATRIX": "tests/full_layer_b_core.rs::full_layer_b_core_differential",
    "RP-OBJ-FV": "tests/phase2.rs::n3_a7_a4_and_reorg",
    "RP-OBJ-CP": "tests/phase2.rs::checkpoint_mode_is_explicit_and_carries_its_scope",
    "CK-OBJ-CP": "tests/phase2.rs::checkpoint_mode_is_explicit_and_carries_its_scope",
    "CK-OBJ-FV": "tests/phase2.rs::ckn_needs_checkpoint_trust",
}
PRIMARY = (
    "DIRECT_REPLAY", "ROOTED_MAPPING", "INTENTIONAL_STRICTER_REJECTION",
    "NOT_PORTABLE", "MISSING_AUTHORITATIVE_INPUT", "DIVERGES",
)


def main() -> None:
    assert sys.argv[1:] in ([], ["--check"]), "usage: build-phase-2-matrix.py [--check]"
    assert subprocess.check_output(["git", "-C", str(RESEARCH), "rev-parse", "HEAD"], text=True).strip() == HEAD
    frozen = json.loads((RESEARCH / "post-h1f/layer-b/layer-b-result.json").read_text())
    rooted = json.loads((RESEARCH / "post-h1f/h1-rooted/h1-rooted-result.json").read_text())
    replay_rows = list(map(json.loads, (HERE / "evidence/phase-2/layer-b-core-replay.jsonl").read_text().splitlines()))
    replay_ids = [r["id"] for r in replay_rows]
    assert len(replay_ids) == len(set(replay_ids)), "duplicate core replay ID"
    replay = {r["id"]: (line, r) for line, r in enumerate(replay_rows, 1)}
    rows = []
    ids = set()
    source_ids = [r["id"] for group in GROUPS for r in frozen[group]] + [r["id"] for r in rooted["key_rows"]]
    duplicates = sorted(k for k, v in collections.Counter(source_ids).items() if v > 1)
    assert not duplicates, f"duplicate authoritative case IDs: {duplicates}"
    assert set(replay) - set(source_ids) == {"KEY-R2B", "POL-ASSET-NETINDEP", "POL-H1C-ASSET"}, "unexpected replay setup IDs"
    for group in GROUPS:
        for src in frozen[group]:
            case_id = src["id"]
            assert case_id not in ids, case_id
            ids.add(case_id)
            assert case_id in replay, f"missing core replay: {case_id}"
            replay_line, observed = replay[case_id]
            assert observed["actual"] == src["actual"], case_id
            production = observed.get("production_result")
            reason = None
            if production is None:
                status = "NOT_PORTABLE"
                reason = REASONS[case_id]
                primary = "NOT_PORTABLE"
                location = f"{RESEARCH.name}/post-h1f/layer-b/layer-b-result.json#{group}[id={case_id}]"
            elif production == src["actual"]:
                status = "AGREES"
                primary = "DIRECT_REPLAY"
                location = f"tests/full_layer_b_core.rs::full_layer_b_core_differential; evidence/phase-2/layer-b-core-replay.jsonl:{replay_line}"
            elif production == "ACCEPT" and src["actual"] != "ACCEPT":
                status = "DIVERGES"
                primary = "DIVERGES"
                location = f"evidence/phase-2/layer-b-core-replay.jsonl:{replay_line}"
            else:
                status = "DIVERGES"
                primary = "DIVERGES"
                location = f"evidence/phase-2/layer-b-core-replay.jsonl:{replay_line}"
            rows.append({"case_id": case_id, "category": observed["group"], "research_expected": src["expected"],
                         "research_category": group, "research_actual": src["actual"], "production_result": production,
                         "status": status, "primary_classification": primary,
                         "test_evidence_location": location, "reason_if_not_portable": reason,
                         "source": f"post-h1f/layer-b/layer-b-result.json#{group}"})
    assert len(ids) == 218, len(ids)
    for src in rooted["key_rows"]:
        case_id = src["id"]
        assert case_id not in ids, case_id
        ids.add(case_id)
        if case_id in ROOTED:
            production, status, note = ROOTED[case_id]
            reason = None
            primary = "INTENTIONAL_STRICTER_REJECTION" if status == "INTENTIONAL_STRICTER_REJECTION" else "ROOTED_MAPPING"
            location = ROOTED_TESTS[case_id]
        else:
            production, status, note = None, "NOT_PORTABLE", ""
            reason = ROOTED_REASONS[case_id]
            primary = "NOT_PORTABLE"
            location = f"{RESEARCH.name}/post-h1f/h1-rooted/h1-rooted-result.json#key_rows[id={case_id}]"
        rows.append({"case_id": case_id, "category": "h1-rooted-key", "research_expected": src["expected"],
                     "research_category": "key_rows", "research_actual": src["actual"], "production_result": production,
                     "status": status, "primary_classification": primary,
                     "test_evidence_location": location, "reason_if_not_portable": reason, "note": note,
                     "source": "post-h1f/h1-rooted/h1-rooted-result.json#key_rows"})
    manifest_ids = [r["case_id"] for r in rows]
    missing = sorted(set(source_ids) - set(manifest_ids))
    unclassified = sorted(r["case_id"] for r in rows if r.get("primary_classification") not in PRIMARY)
    assert len(rows) == 236 and not missing and not unclassified, (len(rows), missing, unclassified)
    counts = collections.Counter(r["status"] for r in rows)
    primary_counts = collections.Counter(r["primary_classification"] for r in rows)
    primary_counts = {key: primary_counts[key] for key in PRIMARY}
    assert len(set(manifest_ids)) == len(rows) == sum(primary_counts.values())
    assert not primary_counts["DIVERGES"], "unexplained divergence"
    assert not primary_counts["MISSING_AUTHORITATIVE_INPUT"], "missing authoritative input"
    for r in rows:
        if r["production_result"] == "ACCEPT" and r["research_actual"] != "ACCEPT":
            raise AssertionError(f"production ACCEPT after research rejection: {r['case_id']}")
        if r["primary_classification"] == "NOT_PORTABLE":
            assert r["reason_if_not_portable"], r["case_id"]
        if r["primary_classification"] == "INTENTIONAL_STRICTER_REJECTION":
            assert r["note"] and r["production_result"] != "ACCEPT", r["case_id"]
        assert r["test_evidence_location"], r["case_id"]
    applicable = len(rows) - primary_counts["NOT_PORTABLE"] - primary_counts["MISSING_AUTHORITATIVE_INPUT"]
    core_replayed = primary_counts["DIRECT_REPLAY"]
    rooted_mapped = primary_counts["ROOTED_MAPPING"] + primary_counts["INTENTIONAL_STRICTER_REJECTION"]
    assert core_replayed == 196 and rooted_mapped == 6 and applicable == 202
    manifest = {"research_commit": HEAD, "scope": "218 frozen Layer-B vectors plus 18 H1-rooted key rows",
                "total_authoritative_cases": len(rows), "unique_authoritative_case_count": len(set(source_ids)),
                "total_manifest_entries": len(rows), "unique_case_ids": len(set(manifest_ids)),
                "duplicate_case_ids": duplicates, "missing_case_ids": missing, "unclassified_case_ids": unclassified,
                "primary_classification_counts": primary_counts,
                "applicable_cases": applicable,
                "core_replayed_cases": core_replayed, "rooted_mapped_cases": rooted_mapped,
                "agreements": counts["AGREES"], "intentional_stricter_rejections": counts["INTENTIONAL_STRICTER_REJECTION"],
                "not_portable": counts["NOT_PORTABLE"], "missing_authoritative_input": counts["MISSING_AUTHORITATIVE_INPUT"],
                "divergences": counts["DIVERGES"], "cases": rows}
    rendered = json.dumps(manifest, indent=2) + "\n"
    if sys.argv[1:] == ["--check"]:
        assert OUT.read_text() == rendered, "committed Phase-2 case manifest differs from frozen research/replay inventory"
    else:
        OUT.write_text(rendered)
    print({k: manifest[k] for k in ("total_authoritative_cases", "total_manifest_entries", "unique_case_ids", "duplicate_case_ids", "missing_case_ids", "unclassified_case_ids", "primary_classification_counts", "agreements", "divergences")})


if __name__ == "__main__":
    main()
