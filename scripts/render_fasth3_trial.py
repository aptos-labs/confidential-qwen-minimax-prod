"""Render a temporary FastH3 trial from a released runtime; never deploy it."""

from __future__ import annotations

import argparse
import copy
import re
from pathlib import Path
from typing import Any

import yaml

IMAGE = "us-docker.pkg.dev/aptos-registry/ccs-images-public/confidential-qwen-minimax-fasth3"


def render(base: dict[str, Any], image: str) -> dict[str, Any]:
    if not re.fullmatch(re.escape(IMAGE) + r"@sha256:[0-9a-f]{64}", image):
        raise ValueError("verified public GAR FastH3 digest required")
    result = copy.deepcopy(base)
    containers = {c["name"]: c for c in result["containers"]}
    if set(containers) != {"litellm", "vllm-omni-qwen", "vllm-omni-minimax", "moderation-worker"}:
        raise ValueError("expected the released moderated runtime")
    model = next(m for m in result["models"] if m["name"] == "minimax-h3-fl2va")
    root_hash = model["mpk"].split("_")[0]
    if not re.fullmatch(r"[0-9a-f]{64}", root_hash):
        raise ValueError("invalid measured model root")
    minimax = containers["vllm-omni-minimax"]
    if "CUDA_VISIBLE_DEVICES=0,1,2,3 " not in minimax["command"][0]:
        raise ValueError("unexpected MiniMax GPU placement")
    if minimax["networks"] != ["models"]:
        raise ValueError("MiniMax must remain on the private models network")
    if "CCS_MINIMAX_WATERMARK_ENABLED" not in minimax.get("env", []):
        raise ValueError("expected the watermark-configurable release")
    minimax["image"] = image
    minimax["entrypoint"] = ["/bin/sh", "-c"]
    minimax["command"] = [
        "CUDA_VISIBLE_DEVICES=0,1,2,3 exec python3 /opt/fasth3/serve.py "
        f"--model /tinfoil/mpk/mpk-{root_hash} >/dev/null 2>&1"
    ]
    minimax.setdefault("tmpfs", {})["/var/cache/fasth3"] = "rw,size=4g,mode=0700,exec,nosuid,nodev"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = render(yaml.safe_load(args.baseline.read_text()), args.image)
    with args.output.open("x") as output:
        yaml.safe_dump(result, output, sort_keys=True)


if __name__ == "__main__":
    main()
