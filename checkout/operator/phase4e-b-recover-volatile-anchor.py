"""Audit and recover only the known Zebra 99-block volatile-tip rollback.

This does not restore chain state or rewrite transactions. It changes the
local operator anchor only after independently checking both generations of
accepted transactions against the live, identical-genesis chain.
"""

import hashlib
import json
from pathlib import Path

from exchange.persistent_chain import (_atomic_json, _inspect, _rpc,
                                       _validate_container)

ROOT = Path("/private/tmp/zwa4e-phase4e-b")
BACKUP = Path("/private/tmp/zwa4e-phase4e-b-owner-approved-backup-20261012")
ORDER = "ec27f52d2e7ad69287f7d4d4357025af"
PORT = 18321
CONTAINER = "zwa-phase4e-b"


def main() -> None:
    old = json.loads((BACKUP / "chain-identity.json").read_text())
    manifest = json.loads((BACKUP / "backup-manifest.json").read_text())
    assert hashlib.sha256((BACKUP / "chain-identity.json").read_bytes()).hexdigest() == \
        manifest["sha256"]["chain-identity.json"]
    path = ROOT / "chain-identity.json"
    current = json.loads(path.read_text())
    assert current.get("detectedReset") == "CHAIN_RESET: recorded chain anchor is absent or changed"
    assert old["anchorHeight"] == 113 and current["anchorHeight"] == 2215
    assert old["genesisHash"] == current["genesisHash"]
    info = _inspect(CONTAINER)
    assert info and info["State"]["Running"]
    _validate_container(info, ROOT, PORT)
    tip = _rpc(PORT, "getblockcount")
    assert tip == 2116 and current["anchorHeight"] - tip == 99
    assert _rpc(PORT, "getblockhash", [0]) == old["genesisHash"]
    assert _rpc(PORT, "getblockhash", [113]) == old["anchorHash"]
    historical = json.loads((BACKUP / "exchange/portfolio-after-restart.json").read_text())
    checked = []
    for receipt in historical["receipts"]:
        block = _rpc(PORT, "getblock", [str(receipt["height"]), 1])
        assert receipt["txid"] in block["tx"]
        checked.append({"txid": receipt["txid"], "height": receipt["height"],
                        "blockHash": block["hash"]})
    new = json.loads((ROOT / "exchange/orders" / ORDER / "order-receipt.json").read_text())
    assert new["height"] == 1115 and new["approvalMode"] == "SCRIPTED_TEST_MODE"
    block = _rpc(PORT, "getblock", ["1115", 1])
    assert block["hash"] == new["blockHash"] and new["txid"] in block["tx"]
    checked.append({"txid": new["txid"], "height": 1115, "blockHash": block["hash"]})
    # Anchor the accepted transaction itself, which is 1,001 blocks below tip.
    assert tip - new["height"] >= 1000
    recovered = dict(current)
    recovered.pop("detectedReset")
    recovered["anchorHeight"] = new["height"]
    recovered["anchorHash"] = new["blockHash"]
    recovered["lastObservedHeight"] = tip
    recovered["lastStartState"] = "CHAIN_RESTARTED"
    recovered["volatileAnchorRecovery"] = {
        "droppedEmptyTipBlocks": 99, "previousAnchorHeight": current["anchorHeight"],
        "backupAnchorHeight": old["anchorHeight"], "recoveryEvidence": "phase4e-b-volatile-anchor-recovery.json"}
    audit = ROOT / "exchange/orders" / ORDER / "phase4e-b-volatile-anchor-recovery.json"
    if audit.exists():
        raise RuntimeError("recovery audit already exists")
    audit.write_text(json.dumps({"oldIdentity": current, "newIdentity": recovered,
                                 "liveTip": tip, "checkedTransactions": checked}, indent=2) + "\n")
    audit.chmod(0o600)
    _atomic_json(path, recovered)
    print(json.dumps({"state": "CHAIN_RESTARTED", "liveTip": tip,
                      "stableAnchorHeight": new["height"],
                      "verifiedTransactions": len(checked)}))


if __name__ == "__main__":
    main()
