"""Operator-owned public event file. No wallet material belongs in this file."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

from .api import FAILURE_STAGES, PUBLIC_STAGES, SessionError, read_event_record, read_session


def append(session_path: Path, event_path: Path, stage: str, mode: str = "LOCAL_OPERATOR_EVENT_ONLY") -> None:
    session = read_session(session_path)
    if session is None:
        raise SessionError("session result absent")
    session_id = session["sessionId"]
    stages, existing_mode = read_event_record(event_path, session_id)
    if stages and existing_mode != mode:
        raise SessionError("operator event mode changed")
    if stage in FAILURE_STAGES:
        if stages and stages[-1] in FAILURE_STAGES:
            raise SessionError("terminal event already recorded")
        if stage == "BUYER_APPROVAL_NOT_GRANTED" and stages[-1:] != ["AWAITING_BUYER_APPROVAL"]:
            raise SessionError("buyer approval failure out of order")
        if stage == "SELLER_APPROVAL_NOT_GRANTED" and stages[-1:] != ["AWAITING_SELLER_APPROVAL"]:
            raise SessionError("seller approval failure out of order")
    elif (stage not in PUBLIC_STAGES or len(stages) >= len(PUBLIC_STAGES)
          or stages != list(PUBLIC_STAGES[:len(stages)])
          or stage != PUBLIC_STAGES[len(stages)]):
        raise SessionError("event out of order")
    if stage in ("TRANSACTION_SUBMITTED", "TRANSACTION_INCLUDED", "CONFIRMATION_DEPTH_OBSERVED", "RECEIPT_AVAILABLE") and session["receipt"] is None:
        raise SessionError("settlement event without receipt")
    if stage == "RECEIPT_AVAILABLE" and session["outcome"] != "ACCEPTED":
        raise SessionError("receipt not accepted")
    payload = {"version": 1, "sessionId": session_id, "stages": stages + [stage]}
    if mode == "SCRIPTED_TEST_MODE":
        payload["mode"] = mode
    event_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".event-", dir=event_path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(payload, out, separators=(",", ":"))
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, event_path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Record a public local checkout observation")
    parser.add_argument("session", type=Path)
    parser.add_argument("events", type=Path)
    parser.add_argument("stage", choices=PUBLIC_STAGES + FAILURE_STAGES)
    parser.add_argument("--mode", choices=("LOCAL_OPERATOR_EVENT_ONLY", "SCRIPTED_TEST_MODE"),
                        default="LOCAL_OPERATOR_EVENT_ONLY")
    args = parser.parse_args()
    append(args.session, args.events, args.stage, args.mode)


if __name__ == "__main__":
    main()
