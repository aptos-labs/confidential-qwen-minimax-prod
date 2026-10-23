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


def render(
    base: dict[str, Any],
    gateway: str,
    worker: str,
    *,
    minimax_image: str | None = None,
    minimax_pack: str | None = None,
    minimax_repo: str | None = None,
    voice_samples: bool = False,
) -> dict[str, Any]:
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
    if voice_samples:
        containers["litellm"]["env"].append("CCS_MODERATION_AUDIO_ENABLED")
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
    if any(value is not None for value in (minimax_image, minimax_pack, minimax_repo)):
        if not all((minimax_image, minimax_pack, minimax_repo)):
            raise ValueError("combined H3 requires an image, model pack and source revision")
        assert minimax_image is not None and minimax_pack is not None and minimax_repo is not None
        if not re.fullmatch(
            re.escape(REGISTRY + "confidential-qwen-minimax-video-controls")
            + r"@sha256:[0-9a-f]{64}",
            minimax_image,
        ):
            raise ValueError("exact public GAR MiniMax controls digest required")
        if not re.fullmatch(r"[0-9a-f]{64}_[1-9][0-9]*_[0-9a-f-]{36}", minimax_pack):
            raise ValueError("verified combined model pack required")
        if not re.fullmatch(r"MiniMaxAI/MiniMax-H3@[0-9a-f]{40}", minimax_repo):
            raise ValueError("pinned MiniMax source revision required")
        model = next(m for m in runtime["models"] if m["name"] == "minimax-h3-fl2va")
        old_path = "/tinfoil/mpk/mpk-" + model["mpk"].split("_")[0] + "/FL2VA"
        new_path = "/tinfoil/mpk/mpk-" + minimax_pack.split("_")[0]
        command = minimax["command"][0]
        if command.count(old_path) != 1 or command.count("--usp=4") != 1:
            raise ValueError("unexpected MiniMax launch command")
        # Ulysses replicates both DiTs on each GPU. Shard their weights and the
        # shared text encoder across the same four H200s for the combined model.
        minimax["command"] = [
            command.replace(old_path, new_path, 1).replace(
                "--usp=4", "--tensor-parallel-size=4 --text-encoder-tp-size=4 --usp=1", 1
            )
        ]
        minimax["image"] = minimax_image
        model.update(mpk=minimax_pack, repo=minimax_repo)
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
    parser.add_argument("--minimax-image")
    parser.add_argument("--minimax-pack")
    parser.add_argument("--minimax-repo")
    parser.add_argument("--voice-samples", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    value = render(
        yaml.safe_load(BASELINE.read_text()),
        args.gateway,
        args.worker,
        minimax_image=args.minimax_image,
        minimax_pack=args.minimax_pack,
        minimax_repo=args.minimax_repo,
        voice_samples=args.voice_samples,
    )
    with args.output.open("x") as output:
        yaml.safe_dump(value, output, sort_keys=True)


if __name__ == "__main__":
    main()
