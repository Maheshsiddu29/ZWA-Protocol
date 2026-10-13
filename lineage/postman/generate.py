#!/usr/bin/env python3
"""Build the Phase 3 Postman assets from the pinned public evidence fixture."""

import base64
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURE = HERE.parent / "tests/fixtures/phase2/n3-v3.bin"
N3_SHA256 = "0821a7abafca556bdcf9403ed9e040603194bf5d46650316d42554ee9c96ebb0"
DATA = FIXTURE.read_bytes()
assert hashlib.sha256(DATA).hexdigest() == N3_SHA256


def encoded(data):
    return base64.b64encode(data).decode("ascii")


def changed(offset):
    data = bytearray(DATA)
    data[offset] ^= 1
    return encoded(data)


def exchanged_steps():
    data = bytearray(DATA)
    size = 96 + 2 + 5120
    first = 2429 + size
    second = first + size
    data[first:second], data[second:second + size] = data[second:second + size], data[first:second]
    return encoded(data)


def version_four():
    data = bytearray(DATA)
    data[16] = 4
    return encoded(data)


variables = {
    "n3_evidence_base64": encoded(DATA),
    "tampered_proof_base64": changed(2527 + 100),
    "broken_continuity_base64": exchanged_steps(),
    "invalid_root_base64": changed(230 + 100),
    "invalid_terminal_base64": changed(230 + 1169 + 100),
    "wrong_chain_ref_base64": changed(49 + 16 + 1 + 32 + 4),
    "foreign_vk_base64": changed(17),
    "truncated_evidence_base64": encoded(DATA[:-1]),
    "trailing_bytes_base64": encoded(DATA + b"\x00"),
    "unsupported_version_base64": version_four(),
}


def js_lines(*lines):
    return [{"listen": "test", "script": {"type": "text/javascript", "exec": list(lines)}}]


def request(name, method, path, body=None, tests=(), prerequest=()):
    url = "{{base_url}}" + path
    req = {
        "method": method,
        "header": [{"key": "Accept", "value": "application/json"}],
        "url": {"raw": url, "host": ["{{base_url}}"], "path": path.lstrip("/").split("/")},
    }
    if body is not None:
        req["header"].append({"key": "Content-Type", "value": "application/json"})
        req["body"] = {"mode": "raw", "raw": body, "options": {"raw": {"language": "json"}}}
    events = []
    if prerequest:
        events.append({"listen": "prerequest", "script": {"type": "text/javascript", "exec": list(prerequest)}})
    events += js_lines(*tests)
    return {"name": name, "request": req, "event": events}


def success_tests(status, permission, action=False, code=None, obligation=None, demo=False, snapshot=None):
    prefix = "body.result" if demo else "body"
    lines = [
        'pm.test("HTTP 200", () => pm.response.to.have.status(200));',
        "const body = pm.response.json();",
        f"const result = {prefix};",
        'pm.test("Response shape", () => {',
        '  pm.expect(result).to.have.property("verificationId").that.is.a("string");',
        '  pm.expect(result).to.have.property("verification").that.is.an("object");',
        '  pm.expect(result).to.have.property("timeline").that.is.an("array");',
        '  pm.expect(result).to.have.property("tradePermission").that.is.an("object");',
        '  pm.expect(result.trustSource).to.eql("PINNED_REGTEST_FIXTURE");',
        '  pm.expect(result.verification.mode).to.eql("rooted");',
        '  pm.expect(result.verification.verifier.verifier_id).to.eql("lineage-v2-l1");',
        '  pm.expect(result.verification.verifier.vk_id).to.match(/^[0-9a-f]{64}$/);',
        '  pm.expect(result.verification.obligations).to.be.an("object");',
        '});',
        f'pm.test("Verification status", () => pm.expect(result.verification.status).to.eql("{status}"));',
        f'pm.test("Lineage permission", () => pm.expect(result.lineagePermission).to.eql("{permission}"));',
        f'pm.test("Trade gate", () => pm.expect(result.tradePermission.allowed).to.eql({str(permission == "ALLOWED").lower()}));',
        'pm.test("Eligibility and settlement are not executed", () => { pm.expect(result.eligibility).to.eql("NOT_EVALUATED"); pm.expect(result.settlement).to.eql("NOT_EXECUTED"); });',
        f'pm.test("Action evaluated flag", () => pm.expect(result.actionEvaluated).to.eql({str(action).lower()}));',
        'pm.test("No private witness fields", () => {',
        '  const forbidden = new Set(["witness", "private_witness", "note_opening", "rho", "psi", "rcm", "nk", "recipient_key", "credential_secret", "spending_key", "private_key", "wallet_seed"]);',
        '  function inspect(value) { if (Array.isArray(value)) return value.forEach(inspect); if (value && typeof value === "object") { Object.keys(value).forEach(key => { pm.expect(forbidden.has(key.toLowerCase()), key).to.be.false; inspect(value[key]); }); } }',
        '  inspect(body);',
        '});',
    ]
    if code is not None:
        lines.append(f'pm.test("Verification code", () => pm.expect(result.verification.code).to.eql("{code}"));')
    elif status == "VERIFIED":
        lines.append('pm.test("No failure code", () => pm.expect(result.verification.code).to.be.null);')
    if obligation:
        field, value = obligation
        lines.append(f'pm.test("{field} obligation", () => pm.expect(result.verification.obligations.{field}).to.eql("{value}"));')
    if snapshot:
        lines.append(f'pm.test("Snapshot", () => pm.expect(result.verification.snapshot_id).to.eql("{snapshot}"));')
    if demo:
        lines += [
            'pm.test("Demo observed real expected verdict", () => {',
            f'  pm.expect(body.scenarioId).to.eql("{demo}");',
            f'  pm.expect(body.expected).to.eql("{status}");',
            f'  pm.expect(body.observed).to.eql("{status}");',
            '  pm.expect(body.matchesExpected).to.be.true;',
            '  pm.expect(body.demo).to.be.true;',
            '  pm.expect(body.fixture.sha256).to.match(/^[0-9a-f]{64}$/);',
            '});',
        ]
    return lines


def envelope(variable):
    return json.dumps({"evidenceEncoding": "base64", "evidence": "{{" + variable + "}}"}, separators=(",", ":"))


def error_tests(http, error):
    return [
        f'pm.test("HTTP {http}", () => pm.response.to.have.status({http}));',
        'const body = pm.response.json();',
        f'pm.test("Transport error", () => {{ pm.expect(body.error).to.be.an("object"); pm.expect(body.error.code).to.eql("{error}"); pm.expect(body.error.message).to.be.a("string"); }});',
        'pm.test("No verification or settlement", () => { pm.expect(body).not.to.have.property("verification"); pm.expect(body).not.to.have.property("settlement"); });',
    ]


folders = [
    {"name": "01 Health", "item": [
        request("Live", "GET", "/health/live", tests=[
            'pm.test("HTTP 200", () => pm.response.to.have.status(200));',
            'pm.test("Live status", () => pm.expect(pm.response.json()).to.deep.equal({status:"LIVE"}));',
        ]),
        request("Ready", "GET", "/health/ready", tests=[
            'pm.test("HTTP 200", () => pm.response.to.have.status(200));',
            'const body = pm.response.json();',
            'pm.test("Pinned A7 ready", () => { pm.expect(body.status).to.eql("READY"); pm.expect(body.snapshot).to.eql("A7"); pm.expect(body.trustSource).to.eql("PINNED_REGTEST_FIXTURE"); });',
        ]),
    ]},
    {"name": "02 Verifier Information", "item": [
        request("Verifier information", "GET", "/v1/verifier/info", tests=[
            'pm.test("HTTP 200", () => pm.response.to.have.status(200));',
            'const body = pm.response.json();',
            'pm.test("Pinned verifier identity and policy", () => { pm.expect(body.relation).to.eql("NormalLineageStepV2"); pm.expect(body.verifierId).to.eql("lineage-v2-l1"); pm.expect(body.vkDigest).to.match(/^[0-9a-f]{64}$/); pm.expect(body.parameterIdentity).to.match(/^[0-9a-f]{64}$/); pm.expect(body.finality.k).to.eql(3); pm.expect(body.snapshot.id).to.eql("A7"); pm.expect(body.containerVersions).to.include(3); pm.expect(body.trustSource).to.eql("PINNED_REGTEST_FIXTURE"); });',
        ]),
    ]},
    {"name": "03 Demo Scenarios", "item": [
        request("List all seven scenarios", "GET", "/v1/demo/scenarios", tests=[
            'pm.test("HTTP 200", () => pm.response.to.have.status(200));',
            'const body = pm.response.json();',
            'pm.test("All pinned scenarios", () => { pm.expect(body.trustSource).to.eql("PINNED_REGTEST_FIXTURE"); pm.expect(body.scenarios).to.be.an("array").with.lengthOf(7); pm.expect(body.scenarios.map(x => x.id)).to.have.members(["valid-rooted-n3","pending-a4","checkpoint-ckn","tampered-proof","bad-root","bad-terminal","reorg"]); body.scenarios.forEach(x => { pm.expect(x).to.include.all.keys("id","name","description","expected","snapshot","fixture"); }); });',
        ]),
    ]},
    {"name": "04 Rooted Verification", "item": [
        request("A7 canonical v3 evidence", "POST", "/v1/lineage/verify", envelope("n3_evidence_base64"),
                success_tests("VERIFIED", "ALLOWED", obligation=("finality", "PASSED"), snapshot="A7")),
    ]},
    {"name": "05 Trade Authorization", "item": [
        request("A7 canonical v3 authorization", "POST", "/v1/trade/authorize", envelope("n3_evidence_base64"),
                success_tests("VERIFIED", "ALLOWED", action=True, obligation=("finality", "PASSED"), snapshot="A7")),
        request("Tampered proof authorization denied", "POST", "/v1/trade/authorize", envelope("tampered_proof_base64"),
                success_tests("REJECTED", "DENIED", action=True, code="INVALID_STEP_PROOF", obligation=("design_a", "FAILED"), snapshot="A7")),
        request("Invalid root authorization denied", "POST", "/v1/trade/authorize", envelope("invalid_root_base64"),
                success_tests("REJECTED", "DENIED", action=True, code="ROOT_AUTHENTICATION_FAILED", obligation=("root", "FAILED"), snapshot="A7")),
        request("Foreign VK authorization denied", "POST", "/v1/trade/authorize", envelope("foreign_vk_base64"),
                success_tests("REJECTED", "DENIED", action=True, code="FOREIGN_VERIFIER", obligation=("parse", "FAILED"), snapshot="A7")),
    ]},
    {"name": "06 Negative / Security Tests", "item": []},
]

demo_cases = [
    ("valid-rooted-n3", "VERIFIED", "ALLOWED", None, ("finality", "PASSED"), "A7"),
    ("pending-a4", "PENDING_FINALITY", "DENIED", "REFERENCE_NOT_FINAL", ("finality", "FAILED"), "A4"),
    ("checkpoint-ckn", "CHECKPOINT_TRUST_REQUIRED", "DENIED", "CHECKPOINTED_HISTORY", ("finality", "FAILED"), "CKN"),
    ("tampered-proof", "REJECTED", "DENIED", "INVALID_STEP_PROOF", ("design_a", "FAILED"), "A7"),
    ("bad-root", "REJECTED", "DENIED", "ROOT_AUTHENTICATION_FAILED", ("root", "FAILED"), "A7"),
    ("bad-terminal", "REJECTED", "DENIED", "TERMINAL_AUTHENTICATION_FAILED", ("terminal", "FAILED"), "A7"),
    ("reorg", "INVALIDATED_BY_REORG", "DENIED", "FINALITY_VIOLATION", ("chain_context", "FAILED"), "B8"),
]
for scenario, status, permission, code, obligation, snapshot in demo_cases:
    folders[2]["item"].append(request(scenario, "POST", "/v1/demo/run", json.dumps({"scenarioId": scenario}),
        success_tests(status, permission, code=code, obligation=obligation, demo=scenario, snapshot=snapshot)))

negative = folders[-1]["item"]
for name, variable, status, code, obligation in [
    ("Tampered proof", "tampered_proof_base64", "REJECTED", "INVALID_STEP_PROOF", ("design_a", "FAILED")),
    ("Broken continuity", "broken_continuity_base64", "REJECTED", "BROKEN_CONTINUITY", ("design_a", "FAILED")),
    ("Invalid root", "invalid_root_base64", "REJECTED", "ROOT_AUTHENTICATION_FAILED", ("root", "FAILED")),
    ("Invalid terminal", "invalid_terminal_base64", "REJECTED", "TERMINAL_AUTHENTICATION_FAILED", ("terminal", "FAILED")),
    ("Wrong chain reference", "wrong_chain_ref_base64", "REJECTED", "CHAIN_CONTEXT_REJECTED", ("chain_context", "FAILED")),
    ("Foreign VK inside evidence", "foreign_vk_base64", "REJECTED", "FOREIGN_VERIFIER", ("parse", "FAILED")),
    ("Malformed binary evidence", None, "REJECTED", "INVALID_ENCODING", ("parse", "FAILED")),
    ("Truncated evidence", "truncated_evidence_base64", "REJECTED", "INVALID_ENCODING", ("parse", "FAILED")),
    ("Trailing bytes", "trailing_bytes_base64", "REJECTED", "INVALID_ENCODING", ("parse", "FAILED")),
    ("Unsupported version", "unsupported_version_base64", "UNSUPPORTED", "UNSUPPORTED_VERSION", ("parse", "FAILED")),
]:
    body = envelope(variable) if variable else json.dumps({"evidenceEncoding": "base64", "evidence": encoded(b"garbage")})
    negative.append(request(name, "POST", "/v1/lineage/verify", body,
        success_tests(status, "DENIED", code=code, obligation=obligation, snapshot="A7")))

for name, body, http, code in [
    ("Zero-byte payload", "", 400, "INVALID_JSON"),
    ("Empty JSON object", "{}", 400, "INVALID_JSON"),
    ("Malformed JSON", "{", 400, "INVALID_JSON"),
    ("Invalid base64", json.dumps({"evidenceEncoding": "base64", "evidence": "%%%"}), 400, "INVALID_BASE64"),
    ("Caller chain view override rejected", json.dumps({"evidenceEncoding": "base64", "evidence": "{{n3_evidence_base64}}", "chainView": "A4"}), 400, "INVALID_JSON"),
    ("Caller VK override rejected", json.dumps({"evidenceEncoding": "base64", "evidence": "{{n3_evidence_base64}}", "vkDigest": "0" * 64}), 400, "INVALID_JSON"),
    ("Caller checkpoint trust rejected", json.dumps({"evidenceEncoding": "base64", "evidence": "{{n3_evidence_base64}}", "trustedCheckpoint": True}), 400, "INVALID_JSON"),
    ("Caller finality override rejected", json.dumps({"evidenceEncoding": "base64", "evidence": "{{n3_evidence_base64}}", "finalityK": 0}), 400, "INVALID_JSON"),
    ("Caller verified flag rejected", json.dumps({"evidenceEncoding": "base64", "evidence": "{{n3_evidence_base64}}", "verified": True}), 400, "INVALID_JSON"),
]:
    negative.append(request(name, "POST", "/v1/lineage/verify", body, error_tests(http, code)))

negative.append(request("Oversized request", "POST", "/v1/lineage/verify", "{{oversized_body}}",
    error_tests(413, "REQUEST_TOO_LARGE"),
    prerequest=['pm.variables.set("oversized_body", "{" + "x".repeat(7200001));']))

collection = {
    "info": {
        "name": "ZWA Protocol Phase 3 — rooted lineage API",
        "_postman_id": "0d2fe130-c897-4315-99c5-c4e4252fc6a7",
        "description": "Pinned regtest fixture API validation. Public v3 evidence only. Demo mode and configured A7 view required.",
        "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
    },
    "item": folders,
    "variable": [{"key": key, "value": value, "type": "string"} for key, value in variables.items()],
}
environment = {
    "id": "a2bc8f64-1448-42f1-a96f-959d1d402779",
    "name": "ZWA Local Phase 3",
    "values": [{"key": "base_url", "value": "http://127.0.0.1:3000", "type": "default", "enabled": True}],
    "_postman_variable_scope": "environment",
    "_postman_exported_using": "ZWA Phase 3 fixture generator",
}

(HERE / "ZWA-Phase3.postman_collection.json").write_text(json.dumps(collection, indent=2) + "\n")
(HERE / "ZWA-Local.postman_environment.json").write_text(json.dumps(environment, indent=2) + "\n")
print(f"Generated {sum(len(folder['item']) for folder in folders)} requests from {FIXTURE}")
