"""Concise Markdown report from a run's summary.json (the summary stays authoritative)."""

from __future__ import annotations

GIB = 1 << 30
MIB = 1 << 20

STAGE_ORDER = ("walletSetup", "walletFunding", "reservationRequest", "quoteReadyIncludingQueue", "quotePreparation",
               "groth16ProofGeneration", "groth16Verification", "walletReview", "buyerActionGroupProveAndSign",
               "sellerActionGroupProveAndSign", "coordinatorExecuteLive", "assembleBroadcastInclude",
               "inclusionReconciliation", "receiptGeneration", "confirmToReceipt", "endToEnd",
               "endToEndClientObserved", "durability")

NOT_MEASURED = (
    "Halo2 proving and spend-authorization signing are one pinned tx-tool call per role; they are reported "
    "together (`*ActionGroupProveAndSign`).",
    "Broadcast and regtest block inclusion happen inside one coordinator call; they are reported together "
    "(`assembleBroadcastInclude`). Confirmation means inclusion in the block the coordinator mines, not "
    "consensus finality.",
    "Processes shorter than the sampling interval may be missed by the per-process sampler; their latency is "
    "still bounded by evidence-file times, but their RSS is not.",
    "Container memory is the cgroup charge (anonymous memory plus page cache and kernel memory); it is not the "
    "sum of process RSS.",
    "Purchases serialize on the single demo dealer wallet; concurrency levels measure concurrent submission and "
    "queueing, not parallel proving.",
    "The public web edge (Caddy, Next.js) is not run; requests use the real portal API inside the benchmark "
    "project.",
)


def _b(value) -> str:
    if value is None:
        return "n/a"
    if abs(value) >= GIB:
        return f"{value / GIB:.2f} GiB"
    return f"{value / MIB:.0f} MiB"


def _s(stage) -> str:
    if not stage or stage.get("seconds") is None:
        return "n/a"
    return f"{stage['seconds']:.1f} s"


def render(summary: dict) -> str:
    run, prov = summary.get("run", {}), summary.get("provenance", {})
    lines = [f"# ZWA benchmark: level {summary.get('level')} ({summary.get('result')})", ""]
    if summary.get("fixture"):
        lines += ["> **Test fixture, not a measurement.** Every number below comes from a synthetic fixture "
                  "used to exercise the report generator.", ""]
    lines += [
        f"- Run: `{run.get('runId')}` on project `{run.get('project')}`",
        f"- Started {run.get('startedAt')}, ended {run.get('endedAt')}",
        f"- Source revision: `{prov.get('sourceRevision')}`{' (dirty)' if prov.get('sourceDirty') else ''}",
        f"- Engine image: `{prov.get('engineImageId')}`; Zebra image: `{prov.get('zebraImageId')}`",
        f"- Concurrency: {summary.get('concurrency')}; transactions submitted: "
        f"{sum(1 for t in summary.get('transactions', []) if t.get('orderId'))}",
        "",
    ]
    reason = summary.get("stopReason")
    lines += ["## Result", "", f"**{summary.get('result')}**" +
              (f": stopped by `{reason['code']}`: {reason['detail']}" if reason else ": no stop condition triggered"),
              ""]
    acceptance = summary.get("acceptance") or {}
    durability = ("required for PASS" if acceptance.get("durabilityRequired", summary.get("waitDurability"))
                  else "**not waited for** (`--no-wait-durability`); this PASS does not cover durability")
    lines += [f"- Acceptance: {'passed' if acceptance.get('passed') else 'NOT passed'}",
              f"- Durability: {durability}"]
    monitoring = summary.get("monitoring") or {}
    lines.append(f"- Monitoring: {'valid' if monitoring.get('valid') else 'NOT valid'} "
                 f"({monitoring.get('validSamples')} valid samples, {monitoring.get('sampleErrors')} errors, "
                 f"largest gap {monitoring.get('maxGapSeconds')} s)")
    for problem in acceptance.get("problems") or []:
        lines.append(f"- acceptance: {problem}")
    lines.append("")
    for problem in summary.get("consistencyProblems") or []:
        lines.append(f"- consistency: {problem}")
    if summary.get("consistencyProblems"):
        lines.append("")

    resources = summary.get("resources") or {}
    host = resources.get("host")
    if host:
        lines += ["## Host", "", "| Measure | Value |", "|---|---|",
                  f"| Total RAM | {_b(host['memTotalBytes'])} |",
                  f"| MemAvailable at start / minimum | {_b(host['memAvailableStartBytes'])} / "
                  f"{_b(host['memAvailableMinBytes'])} |",
                  f"| Swap used at start / max (growth) | {_b(host['swapUsedStartBytes'])} / "
                  f"{_b(host['swapUsedMaxBytes'])} ({_b(host['swapGrowthMaxBytes'])}) |",
                  f"| CPU mean / max | {host['cpuPercentMean']} % / {host['cpuPercentMax']} % |",
                  f"| Load average (1 min) max | {host['loadAvg1Max']} |",
                  f"| Kernel OOM kills during run | {host['oomKillDelta']} |"]
        for path, free in (host.get("diskFreeMinBytes") or {}).items():
            lines.append(f"| Disk free minimum `{path}` | {_b(free)} |")
        lines += ["", f"{resources.get('samples')} samples, {resources.get('sampleErrors')} sampling errors.", ""]
    containers = resources.get("containers") or {}
    if containers:
        lines += ["## Containers (cgroup memory)", "",
                  "| Service | Max current | cgroup peak | Limit | CPU s | OOM kills | Restarts | Status |",
                  "|---|---|---|---|---|---|---|---|"]
        for name, c in sorted(containers.items()):
            lines.append(f"| {name} | {_b(c['memoryCurrentMaxBytes'])} | {_b(c['memoryPeakBytes'])} | "
                         f"{_b(c['memoryMaxBytes'])} | {c['cpuSeconds']} | {c['oomKillEvents']} | "
                         f"{c['restartCountEnd']} | {c['statusEnd']} |")
        lines.append("")
    processes = resources.get("processes") or {}
    if processes:
        lines += ["## Processes (peak RSS)", "", "| Service / process | Instances | Peak RSS | Max CPU s |",
                  "|---|---|---|---|"]
        for name, p in processes.items():
            lines.append(f"| {name} | {p['instances']} | {_b(p['peakRssBytes'])} | {p['maxCpuSeconds']} |")
        lines.append("")
    transactions = summary.get("transactions") or []
    if transactions:
        lines += ["## Transactions", ""]
        header = "| Stage | " + " | ".join(t["label"] for t in transactions) + " |"
        lines += [header, "|---|" + "---|" * len(transactions)]
        lines.append("| Outcome | " + " | ".join(t["outcome"] for t in transactions) + " |")
        lines.append("| Final state | " + " | ".join(str(t.get("finalState")) for t in transactions) + " |")
        for stage in STAGE_ORDER:
            lines.append(f"| {stage} | " + " | ".join(_s((t.get("stages") or {}).get(stage)) for t in transactions)
                         + " |")
        lines.append("")
        for t in transactions:
            if t.get("txid"):
                lines.append(f"- {t['label']}: txid `{t['txid']}` at height {t.get('height')} "
                             f"(block `{str(t.get('blockHash'))[:16]}…`), verify-tx included="
                             f"{(t.get('verifyTx') or {}).get('included')}, reconciliation "
                             f"{t['reconciliation']['class']}")
            else:
                lines.append(f"- {t['label']}: no transaction; reconciliation {t['reconciliation']['class']} "
                             f"({t['reconciliation'].get('action')})")
            for problem in t.get("problems") or []:
                lines.append(f"  - {problem['code']}: {problem['detail']}")
        lines.append("")
    lines += ["## Not measured or approximate", ""] + [f"- {item}" for item in NOT_MEASURED] + [""]
    return "\n".join(lines)
