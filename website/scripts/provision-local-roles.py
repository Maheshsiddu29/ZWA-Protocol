#!/usr/bin/env python3
"""Create portal credentials and the session-signing secret for the website.

    provision-local-roles.py <new-file> [account:role ...]

Roles are buyer or seller. The default creates "buyer" and "seller" for the
owner's human-approved companion demo. The hosted judge demo needs only the
dealer operator: `provision-local-roles.py <file> dealer:seller`. Judges never
get an account; they start an anonymous demo session.

The resulting file holds secrets. Keep it private (mode 0600) and never commit
it. Portal credentials never authorize a wallet spend.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    target = Path(sys.argv[1])
    if target.exists():
        raise SystemExit("target already exists; refusing to overwrite")
    pairs = sys.argv[2:] or ["buyer:buyer", "seller:seller"]
    accounts = []
    for pair in pairs:
        account, _, role = pair.partition(":")
        if role not in ("buyer", "seller") or not re.fullmatch(r"[a-z][a-z0-9_-]{2,40}", account):
            raise SystemExit(f"invalid account:role pair {pair!r}")
        password = getpass.getpass(f"New password for {account} ({role}): ")
        if len(password) < 16:
            raise SystemExit("each portal password must have at least 16 characters")
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=64)
        accounts.append({"id": account, "role": role, "salt": salt.hex(), "hash": digest.hex()})
    secret = secrets.token_hex(32)
    contents = "\n".join(("ZWA_SESSION_SECRET=" + shell_quote(secret),
                          "ZWA_ROLE_ACCOUNTS=" + shell_quote(json.dumps(accounts, separators=(",", ":"))), ""))
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(contents)
    print(f"Provisioned {len(accounts)} account(s) in {target}. Load it with `set -a; . {target}; set +a`.")


if __name__ == "__main__":
    main()
