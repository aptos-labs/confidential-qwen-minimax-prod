"""Preserve explicit silent output on the pinned vLLM-Omni v0.30.0 trial."""

from __future__ import annotations

import ast
import hashlib
from importlib.metadata import distribution, version
from pathlib import Path

TARGET = "entrypoints/openai/serving_video.py"
ORIGINAL_SHA256 = "62cf398d04380f05a03097eee8a1ebfbbb0d3a4654615445447209bcfd4735ac"
OLD = "        audios = self._extract_audio_outputs(result, expected_count=len(videos))\n"
NEW = """        audios = (
            [None] * len(videos)
            if "generate_sound" in provided_fields and request.generate_sound is False
            else self._extract_audio_outputs(result, expected_count=len(videos))
        )
"""


def patched(source: bytes) -> bytes:
    if hashlib.sha256(source).hexdigest() != ORIGINAL_SHA256:
        raise ValueError("unexpected vLLM-Omni serving source")
    if source.count(OLD.encode()) != 1:
        raise ValueError("silent-output patch must match exactly once")
    result = source.replace(OLD.encode(), NEW.encode(), 1)
    ast.parse(result)
    return result


def main() -> None:
    if any(version(name) != "0.30.0" for name in ("vllm", "vllm-omni")):
        raise ValueError("trial requires vLLM and vLLM-Omni v0.30.0")
    root = Path(str(distribution("vllm-omni").locate_file("vllm_omni"))).resolve()
    path = root / TARGET
    if path.is_symlink():
        raise ValueError("serving source must be a regular file")
    path.write_bytes(patched(path.read_bytes()))
    for cache in (path.parent / "__pycache__").glob("serving_video.*.pyc"):
        cache.unlink()


if __name__ == "__main__":
    main()
