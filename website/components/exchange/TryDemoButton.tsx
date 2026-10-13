"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

/** Starts a judge session; the server provisions its own disposable wallet. */
export function TryDemoButton({ className = "button button-blue", label = "Try the demo" }: { className?: string; label?: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function start() {
    setBusy(true); setError("");
    try {
      const response = await fetch("/api/demo/session", { method: "POST", credentials: "same-origin",
        headers: { "content-type": "application/json" }, body: "{}" });
      const body = await response.json().catch(() => ({}));
      if (response.ok && body.next === "/wallet") { router.push("/wallet"); router.refresh(); return; }
      setError(body.error ?? "The demo could not be started.");
    } catch { setError("The demo service is unreachable."); }
    setBusy(false);
  }
  return <span className="try-demo">
    <button className={className} type="button" onClick={start} disabled={busy}>{busy ? "Starting…" : label}</button>
    {error && <span className="inline-error" role="alert">{error}</span>}
  </span>;
}
