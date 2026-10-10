#!/usr/bin/env python3
"""Account for every frozen Layer-B and H1-rooted key row.

The test replay file is produced by full_layer_b_core.rs. This script rejects
missing IDs, changes to frozen outcomes, or any production acceptance of a
frozen rejection. It does not classify research facts as verifier calls.
"""

import collections
import json
import subprocess
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


def main() -> None:
    assert subprocess.check_output(["git", "-C", str(RESEARCH), "rev-parse", "HEAD"], text=True).strip() == HEAD
    frozen = json.loads((RESEARCH / "post-h1f/layer-b/layer-b-result.json").read_text())
    rooted = json.loads((RESEARCH / "post-h1f/h1-rooted/h1-rooted-result.json").read_text())
    replay = {r["id"]: r for r in map(json.loads, (HERE / "evidence/phase-2/layer-b-core-replay.jsonl").read_text().splitlines())}
    rows = []
    ids = set()
    for group in GROUPS:
        for src in frozen[group]:
            case_id = src["id"]
            assert case_id not in ids, case_id
            ids.add(case_id)
            observed = replay[case_id]
            assert observed["actual"] == src["actual"], case_id
            production = observed.get("production_result")
            reason = None
            if production is None:
                status = "NOT_PORTABLE"
                reason = REASONS[case_id]
            elif production == src["actual"]:
                status = "AGREES"
            elif production == "ACCEPT" and src["actual"] != "ACCEPT":
                status = "DIVERGES"
            else:
                status = "DIVERGES"
            rows.append({"case_id": case_id, "category": observed["group"], "research_expected": src["expected"],
                         "research_actual": src["actual"], "production_result": production,
                         "status": status, "reason_if_not_portable": reason,
                         "source": f"post-h1f/layer-b/layer-b-result.json#{group}"})
    assert len(ids) == 218, len(ids)
    for src in rooted["key_rows"]:
        case_id = src["id"]
        assert case_id not in ids, case_id
        ids.add(case_id)
        if case_id in ROOTED:
            production, status, note = ROOTED[case_id]
            reason = None
        else:
            production, status, note = None, "NOT_PORTABLE", ""
            reason = ROOTED_REASONS[case_id]
        rows.append({"case_id": case_id, "category": "h1-rooted-key", "research_expected": src["expected"],
                     "research_actual": src["actual"], "production_result": production,
                     "status": status, "reason_if_not_portable": reason, "note": note,
                     "source": "post-h1f/h1-rooted/h1-rooted-result.json#key_rows"})
    assert len(rows) == 236, len(rows)
    counts = collections.Counter(r["status"] for r in rows)
    assert counts["DIVERGES"] == 0, counts
    manifest = {"research_commit": HEAD, "scope": "218 frozen Layer-B vectors plus 18 H1-rooted key rows",
                "total_authoritative_cases": len(rows), "replayed_cases": len(rows) - counts["NOT_PORTABLE"] - counts["MISSING_AUTHORITATIVE_INPUT"],
                "agreements": counts["AGREES"], "intentional_stricter_rejections": counts["INTENTIONAL_STRICTER_REJECTION"],
                "not_portable": counts["NOT_PORTABLE"], "missing_authoritative_input": counts["MISSING_AUTHORITATIVE_INPUT"],
                "divergences": counts["DIVERGES"], "cases": rows}
    OUT.write_text(json.dumps(manifest, indent=2) + "\n")
    print({k: manifest[k] for k in ("total_authoritative_cases", "replayed_cases", "agreements", "intentional_stricter_rejections", "not_portable", "missing_authoritative_input", "divergences")})


if __name__ == "__main__":
    main()
