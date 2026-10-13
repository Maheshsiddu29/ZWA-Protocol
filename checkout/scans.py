"""Extract public balance observations from the independent wallet scanners."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import tempfile

from .api import SessionError, read_scans, read_session

LINE = re.compile(
    r"PHASE35F_BALANCES role=(Buyer|Seller) scanned_height=(\d+) native_zat=(\d+)"
    r" asset=([0-9a-f]{64}) asset_units=(\d+)\Z"
)


def parse(path: Path, role: str, asset_id: str) -> dict:
    if path.is_symlink() or path.stat().st_size > 8192:
        raise SessionError("unsafe scanner output")
    matches = [match for line in path.read_text().splitlines()
               if (match := LINE.fullmatch(line)) is not None]
    if len(matches) != 1 or matches[0].group(1) != role or matches[0].group(4) != asset_id:
        raise SessionError("scanner output does not match role and asset")
    match = matches[0]
    return {"height": int(match.group(2)), "nativeZat": int(match.group(3)),
            "assetUnits": int(match.group(5))}


def write(session_path: Path, output: Path, buyer_before: Path, buyer_after: Path,
          seller_before: Path, seller_after: Path) -> None:
    session = read_session(session_path)
    if session is None or session["receipt"] is None:
        raise SessionError("accepted receipt required for balance publication")
    asset_id = session["quote"]["assetId"]
    data = {"version": 1, "sessionId": session["sessionId"], "assetId": asset_id,
            "buyerBefore": parse(buyer_before, "Buyer", asset_id),
            "buyerAfter": parse(buyer_after, "Buyer", asset_id),
            "sellerBefore": parse(seller_before, "Seller", asset_id),
            "sellerAfter": parse(seller_after, "Seller", asset_id)}
    temp_dir = output.parent
    temp_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".scan-", dir=temp_dir)
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(data, out, separators=(",", ":"))
            out.flush()
            os.fsync(out.fileno())
        read_scans(Path(name), session)
        os.replace(name, output)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish reconciled public wallet scan observations")
    for name in ("session", "output", "buyer_before", "buyer_after", "seller_before", "seller_after"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    write(args.session, args.output, args.buyer_before, args.buyer_after,
          args.seller_before, args.seller_after)


if __name__ == "__main__":
    main()
