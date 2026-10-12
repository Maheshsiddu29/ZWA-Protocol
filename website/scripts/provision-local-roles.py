#!/usr/bin/env python3
"""Create four independent browser portal credentials for a local demo.

The resulting shell file contains sensitive portal credentials. Keep it in a
private temporary directory. These credentials never authorize wallet spends.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: provision-local-roles.py /private/tmp/<new-private-file>")
    target = Path(sys.argv[1])
    if target.parent != Path("/private/tmp") or not target.name.startswith("zwa4d-roles-"):
        raise SystemExit("target must be a new /private/tmp/zwa4d-roles-* file")
    accounts = []
    for role in ("buyer", "seller", "issuer", "auditor"):
        password = getpass.getpass(f"New {role} portal password: ")
        if len(password) < 16:
            raise SystemExit("each portal password must have at least 16 characters")
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=64)
        accounts.append({"id": role, "role": role, "salt": salt.hex(), "hash": digest.hex()})
    secret = secrets.token_hex(32)
    contents = "\n".join(("export ZWA_SESSION_SECRET=" + shell_quote(secret),
                          "export ZWA_ROLE_ACCOUNTS=" + shell_quote(json.dumps(accounts, separators=(",", ":"))), ""))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(target, flags, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(contents)
    print(f"Provisioned four role accounts in {target}; source it only in the local website process.")


if __name__ == "__main__":
    main()
