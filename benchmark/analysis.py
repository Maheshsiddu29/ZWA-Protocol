"""Pure checks and derivations over probe snapshots, client timelines and samples.

No I/O here, so every rule is unit-tested directly.

Stage latencies come from three clocks on the same host kernel:
* the harness's own request and observation times (client side, polled);
* modification times of the order's evidence files, written by the engine
  when each step finishes (nanosecond resolution);
* process start times from /proc/<pid>/stat for the cryptographic tools
  (exact), and the last 1 Hz sample in which each process was seen.
Each latency records which boundaries it used, so a reader can tell an
exact tool runtime from a polled, queue-inclusive upper bound.
"""

from __future__ import annotations

from .engine import PRE_CONFIRM, SETTLED, VERDICT_KEYS
from .safety import REGTEST_GENESIS

RELEASED = ("CANCELLED", "EXPIRED", "REJECTED", "EXECUTION_FAILED")  # reservation returned
DEMO_MODE = "CUSTODIAL_DEMO_POLICY"


# ---------------------------------------------------------------- consistency

def chain_problems(probe: dict) -> list[str]:
    chain, identity = probe.get("chain") or {}, probe.get("identity") or {}
    problems = []
    if not chain.get("live"):
        problems.append("chain RPC is not live")
    elif chain.get("genesisHash") != REGTEST_GENESIS:
        problems.append("chain genesis is not the Zcash regtest genesis")
    if identity.get("genesisHash") != REGTEST_GENESIS:
        problems.append("recorded chain identity is not regtest")
    if probe.get("approvalMode") != DEMO_MODE:
        problems.append(f"exchange approval mode is {probe.get('approvalMode')}, not {DEMO_MODE}")
    if probe.get("integrity") != "ok":
        problems.append("ledger integrity check failed")
    return problems


def inventory_problems(probe: dict) -> list[str]:
    """Reservations and sales must equal what the orders imply; nothing over-reserved."""
    problems = []
    orders = probe.get("orderIndex") or []
    scans = {row["asset_base"]: row for row in probe.get("dealerScans") or []}
    for offer in probe.get("offers") or []:
        mine = [o for o in orders if o["offer_id"] == offer["id"]]
        reserved = sum(o["quantity"] for o in mine if o["state"] not in RELEASED and o["state"] != SETTLED)
        sold = sum(o["quantity"] for o in mine if o["state"] == SETTLED)
        if offer["reserved"] != reserved:
            problems.append(f"offer {offer['id'][:12]}: reserved {offer['reserved']} but live orders hold {reserved}")
        if offer["sold"] != sold:
            problems.append(f"offer {offer['id'][:12]}: sold {offer['sold']} but settled orders total {sold}")
        if offer["reserved"] < 0 or offer["reserved"] + offer["sold"] > offer["capacity"]:
            problems.append(f"offer {offer['id'][:12]}: over-reserved ({offer['reserved']}+{offer['sold']} > "
                            f"{offer['capacity']})")
        scan = scans.get(offer["asset_base"])
        if scan is not None and scan["asset_units"] < offer["reserved"]:
            problems.append(f"offer {offer['id'][:12]}: dealer holds {scan['asset_units']} < reserved "
                            f"{offer['reserved']}")
    settled = {o["id"] for o in orders if o["state"] == SETTLED}
    settlements = {row["order_id"] for row in probe.get("settlements") or []}
    for order_id in settlements - settled:
        state = next((o["state"] for o in orders if o["id"] == order_id), None)
        if state not in ("SUBMITTED", "INCLUDED", "RECONCILED", "UNCERTAIN"):
            problems.append(f"order {order_id[:12]} has a settlement row but state {state}")
    for order_id in settled - settlements:
        problems.append(f"settled order {order_id[:12]} has no settlement row")
    return problems


def sold_delta_problems(before: dict, after: dict, settled_quantities: dict[str, int]) -> list[str]:
    """Units sold during the run must equal what this run's settled orders bought."""
    start = {o["id"]: o["sold"] for o in before.get("offers") or []}
    problems = []
    for offer in after.get("offers") or []:
        delta = offer["sold"] - start.get(offer["id"], 0)
        expected = settled_quantities.get(offer["id"], 0)
        if delta != expected:
            problems.append(f"offer {offer['id'][:12]}: sold changed by {delta}, this run settled {expected}")
    return problems


def receipt_problems(view: dict, detail: dict | None, verify: dict | None, *, quantity: int,
                     payment: int) -> list[tuple[str, str]]:
    """(code, detail) for a settled order whose receipt or inclusion does not check out."""
    out = []
    receipt = view.get("receipt")
    if view.get("state") != SETTLED or not receipt:
        return [("RECEIPT_MISSING", f"state {view.get('state')} without a live receipt")]
    proof = view.get("proof") or {}
    if not view.get("proofVerified") or any(proof.get(k) != "VERIFIED" for k in VERDICT_KEYS):
        out.append(("RECEIPT_INCONSISTENT", "Groth16 authorization verdicts are not all VERIFIED"))
    if view.get("buyerApprovalMode") != DEMO_MODE or view.get("sellerApprovalMode") != DEMO_MODE:
        out.append(("RECEIPT_INCONSISTENT", "both Action Groups were not signed under the demo signer policy"))
    if view.get("effects") != {"assetUnits": quantity, "nativeZat": -payment}:
        out.append(("RECEIPT_INCONSISTENT", f"wallet effects {view.get('effects')} differ from the quote"))
    if receipt.get("quantity") != quantity or receipt.get("paymentZat") != payment:
        out.append(("RECEIPT_INCONSISTENT", "receipt quantity or payment differs from the quote"))
    if detail is not None:
        included = (detail.get("evidence") or {}).get("INCLUDED") or {}
        if included.get("txid") != receipt.get("txid") or included.get("blockHash") != receipt.get("blockHash"):
            out.append(("RECEIPT_INCONSISTENT", "receipt differs from the ledger's inclusion evidence"))
        on_chain = detail.get("onChain") or {}
        if not (on_chain.get("txInBlock") and on_chain.get("blockHashMatches")):
            out.append(("TX_VERIFICATION_FAILED", "independent RPC does not find the transaction in its block"))
    if verify is not None:
        if not (verify.get("found") and verify.get("included") and verify.get("height") == receipt.get("height")
                and verify.get("blockHash") == receipt.get("blockHash")
                and verify.get("genesisHash") == REGTEST_GENESIS):
            out.append(("TX_VERIFICATION_FAILED", "public verify-tx does not confirm inclusion on regtest"))
    return out


# ---------------------------------------------------------------- reconciliation

def reconcile(detail: dict | None, state: str | None) -> dict:
    """Classify one order after a stop. Never infers success; UNCERTAIN stays with the operator."""
    if detail is None and state is None:
        return {"class": "NOT_CREATED", "action": "none"}
    state = state or (detail or {}).get("state")
    evidence = (detail or {}).get("evidence") or {}
    if state == SETTLED:
        on_chain = (detail or {}).get("onChain") or {}
        ok = bool(on_chain.get("txInBlock") and on_chain.get("blockHashMatches"))
        return {"class": "SETTLED" if ok else "SETTLED_UNVERIFIED",
                "action": "none" if ok else "operator: verify the block with an independent RPC"}
    if state in RELEASED:
        failure = evidence.get(state) or {}
        submitted = failure.get("transactionSubmitted")
        return {"class": "NOT_EXECUTED" if submitted is not True else "UNEXPECTED",
                "reason": failure.get("reason"), "action": "none: reservation released by the exchange"}
    if state == "UNCERTAIN":
        return {"class": "UNCERTAIN", "reason": (evidence.get("UNCERTAIN") or {}).get("reason"),
                "action": "wait: the order service releases it only after the expiry height passes and fresh "
                          "scans equal the before-scans; otherwise operator review"}
    if state in PRE_CONFIRM:
        return {"class": "UNCONFIRMED", "action": "safe to cancel through the portal (no signing has started)"}
    return {"class": "IN_PROGRESS", "state": state,
            "action": "wait: confirmed work is never cancelled; re-run reconcile"}


# ---------------------------------------------------------------- latencies

def _span(start: float | None, end: float | None, method: str) -> dict | None:
    if start is None or end is None:
        return None
    seconds = round(end - start, 3)
    if seconds < 0:
        return {"seconds": None, "method": method, "note": "boundaries overlap; not reported"}
    return {"seconds": seconds, "method": method}


def processes_from_samples(samples: list[dict]) -> list[dict]:
    """Distinct process instances seen in samples, keyed by (service, pid, start time)."""
    found: dict[tuple, dict] = {}
    for sample in samples:
        for container in sample.get("containers") or []:
            for proc in container.get("processes") or []:
                key = (container["service"], proc["pid"], proc["startedAt"])
                item = found.setdefault(key, {"service": container["service"], "pid": proc["pid"],
                                              "label": proc["label"], "startedAt": proc["startedAt"],
                                              "firstSeen": sample["t"], "lastSeen": sample["t"],
                                              "peakRssBytes": 0, "cpuSeconds": 0.0, "samples": 0})
                item["lastSeen"] = sample["t"]
                item["samples"] += 1
                item["peakRssBytes"] = max(item["peakRssBytes"], proc.get("peakRssBytes") or 0,
                                           proc.get("rssBytes") or 0)
                item["cpuSeconds"] = max(item["cpuSeconds"], proc.get("cpuSeconds") or 0.0)
    return sorted(found.values(), key=lambda p: (p["startedAt"], p["pid"]))


def _process_before(processes: list[dict], label: str, end: float | None, not_before: float | None) -> dict | None:
    """The latest process with `label` that started in [not_before - 1 s, end]."""
    if end is None:
        return None
    floor = (not_before - 1.0) if not_before is not None else float("-inf")
    candidates = [p for p in processes if p["label"] == label and floor <= p["startedAt"] <= end + 0.5]
    return candidates[-1] if candidates else None


def stage_latencies(detail: dict, timeline: dict, processes: list[dict]) -> dict:
    """Named stage latencies for one order; None where the boundary was not observed."""
    mt = {name: ns / 1e9 for name, ns in (detail.get("artifactMtimeNs") or {}).items()}
    t = timeline
    stages: dict[str, dict | None] = {}
    ops: dict[str, dict] = {}

    def op(name: str, label: str, end: float | None, not_before: float | None) -> float | None:
        proc = _process_before(processes, label, end, not_before)
        if proc:
            ops[name] = {"label": label, "peakRssBytes": proc["peakRssBytes"], "cpuSeconds": proc["cpuSeconds"],
                         "observedSamples": proc["samples"]}
            return proc["startedAt"]
        return None

    stages["walletSetup"] = _span(t.get("walletRequested"), t.get("walletReady"), "client poll (±poll interval)")
    stages["walletFunding"] = _span(t.get("walletFunding"), t.get("walletReady"),
                                    "client poll: FUNDING first seen -> READY (fund tx, 110 durability blocks, scans)")
    stages["reservationRequest"] = _span(t.get("purchaseSubmitted"), t.get("purchaseAccepted"),
                                         "client: portal buy round trip (ledger reservation)")
    stages["quoteReadyIncludingQueue"] = _span(t.get("purchaseSubmitted"), mt.get("quote/terms.json"),
                                               "client submit -> terms.json written")
    scan_start = op("buyerBeforeScan", "tx-tool:signer:balances:buyer", mt.get("buyer-before.log"),
                    t.get("purchaseSubmitted"))
    stages["quotePreparation"] = _span(scan_start, mt.get("quote/terms.json"),
                                       "process start of buyer scan -> terms.json (scans + prepare-sale)")
    generate = op("groth16Generate", "groth16:generate", mt.get("proof-generation.log"), t.get("confirmed"))
    stages["groth16ProofGeneration"] = (
        _span(generate, mt.get("proof-generation.log"), "node generate process start -> proof-generation.log")
        or _span(t.get("confirmed"), mt.get("proof-generation.log"),
                 "upper bound: client confirm -> proof-generation.log (includes service tick wait)"))
    op("groth16Verify", "groth16:verify", mt.get("preflight-authorization.json"), mt.get("proof-generation.log"))
    stages["groth16Verification"] = _span(mt.get("proof-generation.log"), mt.get("preflight-authorization.json"),
                                          "proof-generation.log -> preflight-authorization.json")
    stages["walletReview"] = _span(mt.get("preflight-authorization.json"), mt.get("seller-review.log"),
                                   "preflight-authorization.json -> seller-review.log (both wallet reviews)")
    fallback_start = {"buyer": (mt.get("seller-review.log"), "seller-review.log"),
                      "seller": (mt.get("buyer-signer.log"), "buyer-signer.log")}
    for role in ("buyer", "seller"):
        start = op(f"{role}ActionGroup", f"tx-tool:signer:authorize-live:{role}", mt.get(f"{role}-signer.log"),
                   mt.get("seller-review.log"))
        what = (f"{role} Action Group: Halo2 proving and spend-auth signing (one tool call, not separable)")
        bound, name = fallback_start[role]
        stages[f"{role}ActionGroupProveAndSign"] = (
            _span(start, mt.get(f"{role}-signer.log"), f"authorize-live process start -> {role}-signer.log; {what}")
            or _span(bound, mt.get(f"{role}-signer.log"),
                     f"upper bound: {name} -> {role}-signer.log (includes service and signer polling); {what}"))
    coordinator = op("coordinatorExecuteLive", "tx-tool:coordinator:execute-live", mt.get("receipt.json"),
                     mt.get("seller-review.log"))
    stages["assembleBroadcastInclude"] = _span(mt.get("seller-signer.log"), mt.get("receipt.json"),
                                               "seller-signer.log -> receipt.json (assembly, re-verification, "
                                               "broadcast and regtest block inclusion; one tool call)")
    stages["coordinatorExecuteLive"] = _span(coordinator, mt.get("receipt.json"),
                                             "execute-live process start -> receipt.json")
    stages["inclusionReconciliation"] = _span(mt.get("receipt.json"), mt.get("seller-after.log"),
                                              "receipt.json -> seller-after.log (RPC check + wallet after-scans)")
    stages["receiptGeneration"] = _span(mt.get("seller-after.log"), mt.get("order-receipt.json"),
                                        "seller-after.log -> order-receipt.json")
    stages["confirmToReceipt"] = _span(t.get("confirmed"), mt.get("order-receipt.json"),
                                       "client confirm -> order-receipt.json")
    stages["endToEnd"] = _span(t.get("purchaseSubmitted"), mt.get("order-receipt.json"),
                               "client submit -> order-receipt.json (includes queueing)")
    stages["endToEndClientObserved"] = _span(t.get("purchaseSubmitted"), t.get("receiptSeen"),
                                             "client submit -> RECEIPT_AVAILABLE observed (±poll interval)")
    stages["durability"] = _span(mt.get("order-receipt.json"), mt.get("stabilization.json"),
                                 "order-receipt.json -> stabilization.json (110 empty blocks, checkpoint, rescans)")
    return {"stages": stages, "operations": ops}


def summarize_resources(samples: list[dict]) -> dict:
    """Min/max/peak figures over a run's samples, per host and per container."""
    hosts = [s["host"] for s in samples if "host" in s]
    out: dict = {"samples": len(samples), "sampleErrors": sum(1 for s in samples if "error" in s)}
    if not hosts:
        return out
    cpu = [h["cpuPercent"] for h in hosts if h.get("cpuPercent") is not None]
    out["host"] = {
        "memTotalBytes": hosts[0]["memTotal"],
        "memAvailableMinBytes": min(h["memAvailable"] for h in hosts),
        "memAvailableStartBytes": hosts[0]["memAvailable"],
        "swapUsedStartBytes": hosts[0]["swapUsed"], "swapUsedMaxBytes": max(h["swapUsed"] for h in hosts),
        "swapGrowthMaxBytes": max(h["swapUsed"] for h in hosts) - hosts[0]["swapUsed"],
        "cpuPercentMax": max(cpu) if cpu else None,
        "cpuPercentMean": round(sum(cpu) / len(cpu), 2) if cpu else None,
        "loadAvg1Max": max(h["loadAvg"][0] for h in hosts),
        "oomKillDelta": ((hosts[-1].get("oomKillCount") or 0) - (hosts[0].get("oomKillCount") or 0)
                         if hosts[0].get("oomKillCount") is not None else None),
        "diskFreeMinBytes": {path: min(h["disk"][path]["freeBytes"] for h in hosts if path in h.get("disk", {}))
                             for path in hosts[0].get("disk", {})},
    }
    containers: dict[str, dict] = {}
    for sample in samples:
        for c in sample.get("containers") or []:
            item = containers.setdefault(c["service"], {"memoryCurrentMaxBytes": 0, "memoryPeakBytes": None,
                                                         "memoryMaxBytes": c.get("memoryMax"),
                                                         "oomKillEvents": 0, "cpuUsageUsecStart": c.get("cpuUsageUsec"),
                                                         "cpuUsageUsecEnd": c.get("cpuUsageUsec"),
                                                         "throttledUsecEnd": c.get("cpuThrottledUsec"),
                                                         "restartCountEnd": None, "statusEnd": None})
            if not c.get("present"):
                continue
            item["memoryCurrentMaxBytes"] = max(item["memoryCurrentMaxBytes"], c.get("memoryCurrent") or 0)
            if c.get("memoryPeak") is not None:
                item["memoryPeakBytes"] = max(item["memoryPeakBytes"] or 0, c["memoryPeak"])
            item["oomKillEvents"] = max(item["oomKillEvents"], c.get("oomKillEvents") or 0)
            item["cpuUsageUsecEnd"] = c.get("cpuUsageUsec")
            item["throttledUsecEnd"] = c.get("cpuThrottledUsec")
            state = c.get("state") or {}
            item["restartCountEnd"] = state.get("restartCount", item["restartCountEnd"])
            item["statusEnd"] = state.get("status", item["statusEnd"])
    for item in containers.values():
        start, end = item.pop("cpuUsageUsecStart"), item.pop("cpuUsageUsecEnd")
        item["cpuSeconds"] = round((end - start) / 1e6, 2) if start is not None and end is not None else None
    out["containers"] = containers
    peaks: dict[str, dict] = {}
    for proc in processes_from_samples(samples):
        key = f"{proc['service']}/{proc['label']}"
        item = peaks.setdefault(key, {"instances": 0, "peakRssBytes": 0, "maxCpuSeconds": 0.0})
        item["instances"] += 1
        item["peakRssBytes"] = max(item["peakRssBytes"], proc["peakRssBytes"])
        item["maxCpuSeconds"] = max(item["maxCpuSeconds"], proc["cpuSeconds"])
    out["processes"] = dict(sorted(peaks.items()))
    return out
