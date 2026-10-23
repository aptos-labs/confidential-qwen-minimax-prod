"""Render a reviewable 4/3/1 runtime using verified GAR digests; never deploy it."""

from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "runtime-baselines/v0.2.1.yml"
REGISTRY = "us-docker.pkg.dev/aptos-registry/ccs-images-public/"


def render(base: dict[str, Any], gateway: str, worker: str) -> dict[str, Any]:
    for image, name in (
        (gateway, "confidential-qwen-minimax-paid-gateway"),
        (worker, "confidential-qwen-minimax-moderation-worker"),
    ):
        if not re.fullmatch(re.escape(REGISTRY + name) + r"@sha256:[0-9a-f]{64}", image):
            raise ValueError("exact public GAR digest required")
    runtime = copy.deepcopy(base)
    containers = {c["name"]: c for c in runtime["containers"]}
    if set(containers) != {"litellm", "vllm-omni-qwen", "vllm-omni-minimax"}:
        raise ValueError("expected the reviewed unmoderated baseline")
    containers["litellm"]["image"] = gateway
    containers["litellm"]["env"] = [
        "CCS_MODERATION_PRE_ENABLED",
        "CCS_MODERATION_POST_ENABLED",
    ]
    qwen = containers["vllm-omni-qwen"]
    command = qwen["command"][0]
    expected = "CUDA_VISIBLE_DEVICES=4,5,6,7"
    if expected not in command:
        raise ValueError("unexpected Qwen placement")
    command = command.replace(expected, "CUDA_VISIBLE_DEVICES=4,5,6")
    overrides = {
        "1": {"num_replicas": 1, "devices": "1"},
        "2": {"num_replicas": 1, "devices": "2"},
    }
    command, count = re.subn(
        r"--stage-overrides '[^']+'", "--stage-overrides '" + json.dumps(overrides) + "'", command
    )
    if count != 1:
        raise ValueError("unexpected Qwen stages")
    # Backend error paths may print user inputs even with request logging disabled.
    # Drop stdout/stderr at the process boundary; health/aggregate metrics still work.
    qwen["command"] = [command.rstrip() + " >/dev/null 2>&1"]
    minimax = containers["vllm-omni-minimax"]
    minimax["command"] = [minimax["command"][0].rstrip() + " >/dev/null 2>&1"]
    runtime["containers"].append(
        {
            "name": "moderation-worker",
            "image": worker,
            "networks": ["models"],
            "runtime": "nvidia",
            "gpus": "all",
            "ipc": "private",
            "entrypoint": ["/bin/sh", "-c"],
            "command": [
                "TRITON_CACHE_DIR=/var/cache/moderation/triton "
                "CUDA_VISIBLE_DEVICES=7 exec python3 -m moderation_worker"
            ],
            "restart": "always",
            "shm_size": "1g",
            "tmpfs": {
                "/tmp": "size=2g,mode=1777,noexec",
                "/root": "size=64m,mode=0700,noexec",
                # PyTorch eager CUDA ops load Triton-generated shared libraries.
                "/var/cache/moderation": "rw,size=256m,mode=0700,exec,nosuid,nodev",
            },
            "healthcheck": {
                "test": [
                    "CMD",
                    "python3",
                    "-c",
                    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health/liveliness', timeout=3).close()",
                ],
                "interval": "30s",
                "timeout": "10s",
                "start_period": "60m",
            },
        }
    )
    return runtime


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--worker", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    value = render(yaml.safe_load(BASELINE.read_text()), args.gateway, args.worker)
    with args.output.open("x") as output:
        yaml.safe_dump(value, output, sort_keys=True)


if __name__ == "__main__":
    main()
