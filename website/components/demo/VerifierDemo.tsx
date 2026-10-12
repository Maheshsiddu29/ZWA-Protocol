"use client";

import { useEffect, useState } from "react";

type Verification = { status: string; code: string | null; steps?: number; snapshot_id?: string; obligations?: Record<string,string>; finality_state?: string };
type Result = { verification?: Verification; verificationId?: string; trustSource?: string; lineagePermission?: string; eligibility?: string; settlement?: string; timeline?: { stage:string; status:string }[]; error?: { code:string; message?:string } };
type Demo = { result?: Result; observed?: string; expected?: string; matchesExpected?: boolean; snapshot?: string; fixture?: { name:string; sha256:string }; error?: { code:string; message?:string } };
const scenarios = [
  ["valid-rooted-n3", "Supported lineage"], ["pending-a4", "Pending finality"], ["checkpoint-ckn", "Checkpoint trust"],
  ["tampered-proof", "Tampered proof"], ["bad-root", "Invalid root"], ["bad-terminal", "Invalid terminal"], ["reorg", "Reorg invalidation"],
] as const;
export function VerifierDemo() {
  const [id, setId] = useState<string>(scenarios[0][0]);
  const [loading, setLoading] = useState(false);
  const [availability, setAvailability] = useState("Checking local verifier…");
  const [response, setResponse] = useState<Demo | null>(null);
  const [file, setFile] = useState<File | null>(null);
  useEffect(() => { fetch("/api/verifier/scenarios").then(async r => { const body=await r.json(); setAvailability(r.ok && !body.error ? "Local verifier demo is available" : "Local verifier demo is unavailable. Start it with ZWA_DEMO_ENABLED=true."); }).catch(() => setAvailability("Local verifier demo is unavailable.")); }, []);
  async function runScenario() {
    setLoading(true); setResponse(null);
    try { const r=await fetch("/api/verifier/run", { method:"POST", headers:{"content-type":"application/json"}, body:JSON.stringify({scenarioId:id}) }); setResponse(await r.json()); }
    catch { setResponse({error:{code:"BACKEND_UNAVAILABLE"}}); }
    finally { setLoading(false); }
  }
  async function verifyFile() {
    if (!file) return;
    setLoading(true); setResponse(null);
    try {
      if (file.size > 5_345_661) throw new Error("Evidence exceeds the backend's 5,345,661-byte limit.");
      const encoded = await new Promise<string>((resolve,reject) => { const reader=new FileReader(); reader.onload=()=>resolve(String(reader.result).split(",")[1] || ""); reader.onerror=()=>reject(new Error("Could not read file")); reader.readAsDataURL(file); });
      const r=await fetch("/api/verifier/verify", {method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({evidenceEncoding:"base64",evidence:encoded})});
      setResponse({result:await r.json()});
    } catch (error) { setResponse({error:{code:"FILE_ERROR",message:error instanceof Error?error.message:"Could not verify file"}}); }
    finally { setLoading(false); }
  }
  const result = response?.result;
  const error = response?.error || result?.error;
  const status = result?.verification?.status || response?.observed;
  return <div className="demo-layout"><div className="demo-control">
    <p className="eyebrow">Live local verifier</p><h2>Ask the verifier.</h2><p className="demo-muted">Run a pinned fixture through the real cryptographic backend, or submit a canonical v3 evidence container. The browser sends proof bytes only to this site&apos;s server route.</p>
    <p className="service-state">{availability}</p>
    <label htmlFor="scenario">Fixed research scenario</label><select id="scenario" value={id} onChange={e=>setId(e.target.value)}>{scenarios.map(([value,label])=><option key={value} value={value}>{label}</option>)}</select>
    <button className="button button-blue" type="button" disabled={loading} onClick={runScenario}>{loading?"Evaluating…":"Run scenario ↗"}</button>
    <div className="demo-divider">or verify a local proof container</div>
    <label htmlFor="evidence">Canonical rooted v3 evidence file</label><input id="evidence" type="file" accept=".bin,application/octet-stream" onChange={e=>setFile(e.target.files?.[0] || null)} />
    <button className="button button-outline" type="button" disabled={!file || loading} onClick={verifyFile}>Verify selected file ↗</button>
  </div><div className="demo-result" aria-live="polite">
    <p className="eyebrow eyebrow-light">Evaluation / verdict</p>
    {!response && <div className="empty-result"><img src="/illustrations/verification-rings-empty.svg" alt="" /><p>Select a scenario or evidence file to request a real backend verdict.</p></div>}
    {error && <div className="error-result"><h3>{error.code.replaceAll("_"," ")}</h3><p>{error.message || "The local service could not complete this request. No verification verdict was produced."}</p></div>}
    {status && <><h3 className={`verdict verdict-${status.toLowerCase()}`}>{status.replaceAll("_"," ")}</h3><p className="verdict-meta">{result?.verification?.code ? `Reason: ${result.verification.code}` : "Rooted lineage gate result"}</p>
      <dl className="verdict-facts"><div><dt>Trust source</dt><dd>{result?.trustSource || "—"}</dd></div><div><dt>Snapshot</dt><dd>{result?.verification?.snapshot_id || response?.snapshot || "—"}</dd></div><div><dt>Lineage permission</dt><dd>{result?.lineagePermission || "—"}</dd></div><div><dt>Eligibility</dt><dd>{result?.eligibility || "—"}</dd></div><div><dt>Settlement</dt><dd>{result?.settlement || "—"}</dd></div><div><dt>Finality</dt><dd>{result?.verification?.finality_state || "—"}</dd></div></dl>
      {result?.timeline && <ol className="obligations">{result.timeline.map(item=><li key={item.stage}><span>{item.stage.replaceAll("_"," ")}</span><strong>{item.status}</strong></li>)}</ol>}
      {result?.verificationId && <p className="verification-id">Verification ID · {result.verificationId}</p>}
      <p className="demo-caveat">A verified lineage is scoped to the pinned research view. Investor eligibility is not evaluated and settlement is not executed by this API.</p>
    </>}
  </div></div>;
}
