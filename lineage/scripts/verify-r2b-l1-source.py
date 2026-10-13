#!/usr/bin/env python3
"""Check pinned local R2B-L1 and Orchard source; compare originals when present."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys

lineage = Path(__file__).resolve().parents[1]
root = lineage / 'vendor/r2b-l1'
manifest = json.loads((root / 'SOURCE_MANIFEST.json').read_text())
research = lineage.parent.parent / 'ZWA-Lineage-Research'
upstream = lineage.parent.parent / 'zsa-feasibility/.cargo-home/git/checkouts/orchard-c50baac9ba0a1173/cf801a5/src'
for row in manifest['files']:
    path = root / row['path']
    if hashlib.sha256(path.read_bytes()).hexdigest() != row['packaged_sha256']:
        sys.exit(f'FAIL: packaged source hash: {row["path"]}')
    if row['path'].startswith('src/') and research.is_dir():
        original = research / manifest['research_path'] / row['path']
        if hashlib.sha256(original.read_bytes()).hexdigest() != row['research_sha256']:
            sys.exit(f'FAIL: research source hash: {row["path"]}')
        def rewrite(match):
            target = Path(match.group(1))
            local = root / 'orchard-src' / target.relative_to(upstream)
            return '#[path = "' + os.path.relpath(local, path.parent) + '"]'
        expected = re.sub(r'#\[path = "([^"]+/cf801a5/src/[^"]+)"\]', rewrite,
                          original.read_text()).encode()
        if expected != path.read_bytes():
            sys.exit(f'FAIL: non-path source change: {row["path"]}')
    if row['path'].startswith('orchard-src/') and upstream.is_dir():
        original = upstream / row['path'].removeprefix('orchard-src/')
        if hashlib.sha256(original.read_bytes()).hexdigest() != row['upstream_sha256']:
            sys.exit(f'FAIL: Orchard source hash: {row["path"]}')
print(f'PASS: {len(manifest["files"])} pinned R2B-L1/Orchard files')
