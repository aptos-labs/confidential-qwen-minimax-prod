"""Launch only the reviewed four-H200, text-only FastH3 trial profile."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

ADAPTER = "/opt/fasth3/adapter_model.safetensors"


def command(model: str) -> list[str]:
    if not re.fullmatch(r"/tinfoil/mpk/mpk-[0-9a-f]{64}", model):
        raise ValueError("a measured local H3 model root is required")
    return [
        "vllm",
        "serve",
        model,
        "--omni",
        "--trust-remote-code",
        "--task-type=fl2va",
        "--host=0.0.0.0",
        "--port=8001",
        "--served-model-name=minimax-h3-fl2va",
        "--lora-path=" + ADAPTER,
        "--num-gpus=4",
        "--tensor-parallel-size=4",
        "--text-encoder-tp-size=4",
        "--usp=1",
        "--ring=1",
        "--vae-patch-parallel-size=4",
        "--vae-parallel-mode=tile",
        "--vae-use-tiling",
        "--diffusion-attention-backend=FASTVIDEO_VSA",
        "--fastvideo-vsa-topk=64",
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    argv = command(args.model)
    # This upstream image has no added visible overlay. Require the currently
    # deployed setting rather than pretending this trial implements watermark-on.
    if os.environ.get("CCS_MINIMAX_WATERMARK_ENABLED") != "false":
        raise ValueError("the FastH3 trial requires watermark=false")
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "0,1,2,3":
        raise ValueError("the FastH3 trial is restricted to GPUs 0-3")
    if not (Path(args.model) / "FL2VA/model_index.json").is_file():
        raise ValueError("measured FL2VA base model is missing")
    if not Path(ADAPTER).is_file():
        raise ValueError("pinned FastH3 adapter is missing")
    os.execvp(argv[0], argv)


if __name__ == "__main__":
    main()
