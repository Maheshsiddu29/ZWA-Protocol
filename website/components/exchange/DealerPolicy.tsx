"use client";

import { useState } from "react";
import { postJson } from "@/components/exchange/client-api";

/** Pause or resume the demo dealer's standing signing policy. */
export function DealerPolicy({ initial, csrf }: { initial: "ACTIVE" | "PAUSED"; csrf: string }) {
  const [policy, setPolicy] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function toggle() {
    setBusy(true); setError("");
    const next = policy === "ACTIVE" ? "PAUSED" : "ACTIVE";
    const result = await postJson("/api/exchange/dealer-policy", csrf, { policy: next });
    if (result.ok) setPolicy(result.data.policy); else setError(result.data?.error ?? "Policy was not changed.");
    setBusy(false);
  }
  return <div className="toggle-row">
    <span>{policy === "ACTIVE" ? <span className="badge badge-ok"><span className="status-dot" />Accepting orders</span> : <span className="badge badge-warn">Paused</span>}</span>
    <button className="button button-small button-outline" type="button" disabled={busy} onClick={toggle}>{policy === "ACTIVE" ? "Pause sales" : "Resume sales"}</button>
    {error && <span className="inline-error" role="alert">{error}</span>}
  </div>;
}
