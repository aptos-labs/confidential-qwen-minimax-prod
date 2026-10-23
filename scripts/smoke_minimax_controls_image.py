"""Verify the final model image on CPU without network access or writable rootfs."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = (
    "us-docker.pkg.dev/aptos-registry/ccs-images-public/confidential-qwen-minimax-video-controls"
)
CHECK = r"""
import hashlib, importlib.metadata, json, pathlib, sys
root = pathlib.Path(str(importlib.metadata.distribution('vllm-omni').locate_file('vllm_omni'))).resolve()
artifact = pathlib.Path('/opt/minimax-controls')
expected = pathlib.Path('/expected')
for name in ('install.py', 'manifest.json'):
    assert (artifact / name).read_bytes() == (expected / name).read_bytes(), name
assert not (artifact / 'tests').exists()
for name in ('vllm', 'vllm-omni'):
    assert importlib.metadata.version(name) == '0.28.0'
sys.path.insert(0, str(artifact))
from install import prepare
path, patched = prepare(root)
manifest = json.loads((artifact / 'manifest.json').read_text())
assert path.read_bytes() == patched
assert hashlib.sha256(patched).hexdigest() == manifest['patched_sha256']
print('PASS: silent-output patch installed; original watermark preserved; no GPUs required.')
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("--local", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(re.escape(IMAGE) + r"@sha256:[0-9a-f]{64}", args.image):
        if not args.local or args.image != "ccs-minimax-controls:smoke":
            parser.error("exact public GAR digest required")
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=128",
            "--memory=2g",
            "--cpus=2",
            "--tmpfs=/tmp:rw,size=64m",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "--mount",
            f"type=bind,src={ROOT / 'images/minimax-controls'},dst=/expected,readonly",
            "--entrypoint=python3",
            args.image,
            "-c",
            CHECK,
        ],
        check=True,
        timeout=120,
    )


if __name__ == "__main__":
    main()
