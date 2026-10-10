#!/usr/bin/env python3
"""Verify the repository-local Layer-B core without a research checkout."""
import hashlib
import json
from pathlib import Path
import sys
root=Path(__file__).resolve().parents[1]
manifest=json.loads((root/'vendor/layer-b-core/SOURCE_MANIFEST.json').read_text())
for row in manifest['files']:
    path=root/row['production_path']
    if hashlib.sha256(path.read_bytes()).hexdigest()!=row['production_sha256']:
        sys.exit(f'FAIL: Layer-B source hash: {row["production_path"]}')
print(f'PASS: {len(manifest["files"])} pinned Layer-B files')
