#!/usr/bin/env python3
"""Execute every collection request over HTTP without claiming Postman script execution."""

import json
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
COLLECTION = json.loads((HERE / "ZWA-Phase3.postman_collection.json").read_text())
ENVIRONMENT = json.loads((HERE / "ZWA-Local.postman_environment.json").read_text())
VARIABLES = {row["key"]: row["value"] for row in COLLECTION["variable"]}
VARIABLES.update({row["key"]: row["value"] for row in ENVIRONMENT["values"] if row["enabled"]})
FORBIDDEN = {"witness", "private_witness", "note_opening", "rho", "psi", "rcm", "nk", "recipient_key", "credential_secret", "spending_key", "private_key", "wallet_seed"}


def substitute(value):
    def replacement(match):
        return VARIABLES[match.group(1)]
    return re.sub(r"{{([a-zA-Z0-9_]+)}}", replacement, value)


def forbidden_keys(value):
    if isinstance(value, list):
        return any(forbidden_keys(part) for part in value)
    if isinstance(value, dict):
        return any(key.lower() in FORBIDDEN or forbidden_keys(part) for key, part in value.items())
    return False


def expected(script, expression):
    match = re.search(expression, script)
    return match.group(1) if match else None


def check(name, method, path, response_code, body, script):
    errors = []
    http = expected(script, r"pm\.response\.to\.have\.status\((\d+)\)")
    if http is None or response_code != int(http):
        errors.append(f"HTTP {response_code}, expected {http}")
    error = expected(script, r"body\.error\.code\)\.to\.eql\(\"([^\"]+)\"\)")
    status = expected(script, r"result\.verification\.status\)\.to\.eql\(\"([^\"]+)\"\)")
    permission = expected(script, r"result\.lineagePermission\)\.to\.eql\(\"([^\"]+)\"\)")
    code = expected(script, r"result\.verification\.code\)\.to\.eql\(\"([^\"]+)\"\)")
    obligation = re.search(r"result\.verification\.obligations\.([a-z_]+)\)\.to\.eql\(\"([^\"]+)\"\)", script)
    snapshot = expected(script, r"result\.verification\.snapshot_id\)\.to\.eql\(\"([^\"]+)\"\)")
    if error:
        if body.get("error", {}).get("code") != error:
            errors.append(f"error code {body.get('error')}, expected {error}")
        if "verification" in body or "settlement" in body:
            errors.append("transport error contains verification or settlement")
    elif status:
        result = body.get("result", body)
        verification = result.get("verification", {})
        obligations = verification.get("obligations", {})
        if verification.get("status") != status:
            errors.append(f"status {verification.get('status')}, expected {status}")
        if result.get("lineagePermission") != permission:
            errors.append(f"permission {result.get('lineagePermission')}, expected {permission}")
        if code and verification.get("code") != code:
            errors.append(f"code {verification.get('code')}, expected {code}")
        if status == "VERIFIED" and verification.get("code") is not None:
            errors.append("VERIFIED has failure code")
        if obligation and obligations.get(obligation.group(1)) != obligation.group(2):
            errors.append(f"{obligation.group(1)} obligation mismatch")
        if snapshot and verification.get("snapshot_id") != snapshot:
            errors.append("snapshot mismatch")
        if not isinstance(result.get("verificationId"), str) or not isinstance(result.get("timeline"), list):
            errors.append("required result fields missing")
        verifier = verification.get("verifier", {})
        if verifier.get("verifier_id") != "lineage-v2-l1" or not re.fullmatch(r"[0-9a-f]{64}", verifier.get("vk_id", "")):
            errors.append("verifier identity missing")
        if result.get("eligibility") != "NOT_EVALUATED" or result.get("settlement") != "NOT_EXECUTED":
            errors.append("eligibility/settlement invariant failed")
        if result.get("tradePermission", {}).get("allowed") != (permission == "ALLOWED"):
            errors.append("trade gate mismatch")
        if result.get("actionEvaluated") != (path == "/v1/trade/authorize"):
            errors.append("action flag mismatch")
        if path == "/v1/demo/run":
            if body.get("expected") != status or body.get("observed") != status or body.get("matchesExpected") is not True:
                errors.append("demo expectation mismatch")
    elif path == "/health/live" and body != {"status": "LIVE"}:
        errors.append("live response mismatch")
    elif path == "/health/ready" and (body.get("status"), body.get("snapshot"), body.get("trustSource")) != ("READY", "A7", "PINNED_REGTEST_FIXTURE"):
        errors.append("ready response mismatch")
    elif path == "/v1/verifier/info" and (body.get("relation"), body.get("verifierId"), body.get("finality", {}).get("k"), body.get("snapshot", {}).get("id")) != ("NormalLineageStepV2", "lineage-v2-l1", 3, "A7"):
        errors.append("verifier information mismatch")
    elif path == "/v1/demo/scenarios" and len(body.get("scenarios", [])) != 7:
        errors.append("scenario list mismatch")
    if forbidden_keys(body):
        errors.append("private witness field present")
    return errors


def main():
    records = []
    for folder in COLLECTION["item"]:
        for item in folder["item"]:
            req = item["request"]
            path = "/" + "/".join(req["url"]["path"])
            script = "\n".join(line for event in item["event"] if event["listen"] == "test" for line in event["script"]["exec"])
            raw = req.get("body", {}).get("raw")
            if item["name"] == "Oversized request":
                VARIABLES["oversized_body"] = "{" + "x" * 7_200_001
            data = substitute(raw).encode() if raw is not None else None
            headers = {row["key"]: row["value"] for row in req["header"]}
            request = urllib.request.Request(substitute(req["url"]["raw"]), data=data, headers=headers, method=req["method"])
            started = time.perf_counter()
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    http = response.status
                    payload = response.read()
            except urllib.error.HTTPError as response:
                http = response.code
                payload = response.read()
            elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
            try:
                body = json.loads(payload)
            except ValueError:
                body = {"nonJsonBody": payload.decode(errors="replace")}
            errors = check(item["name"], req["method"], path, http, body, script)
            record = {"folder": folder["name"], "request": item["name"], "method": req["method"], "path": path,
                      "httpStatus": http, "elapsedMs": elapsed_ms, "passed": not errors, "errors": errors, "responseBody": body}
            records.append(record)
            print(f"{'PASS' if not errors else 'FAIL'} {http} {elapsed_ms:.3f}ms {item['name']}")
            if errors:
                print("  " + "; ".join(errors))
    report = {"baseUrl": VARIABLES["base_url"], "collectionRequestsExecuted": False,
              "executionMethod": "Python HTTP equivalent using every generated collection request body and URL",
              "passed": sum(row["passed"] for row in records), "failed": sum(not row["passed"] for row in records), "records": records}
    (HERE / "live-validation.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"Recorded {report['passed']} passed, {report['failed']} failed in {HERE / 'live-validation.json'}")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
