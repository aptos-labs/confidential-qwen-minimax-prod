"""Preserve explicit silent output on the pinned vLLM-Omni v0.30.0 trial."""

from __future__ import annotations

import ast
import hashlib
import shutil
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


FPS = "        output_fps = output_fps_base * self._resolve_video_fps_multiplier(result)\n"
BOUNDED = (
    FPS
    + """        from ccs_fasth3_controls import bound_media
        videos, audios = bound_media(videos, audios, fps=output_fps, sample_rate=audio_sample_rate)
"""
)


def patched(source: bytes) -> bytes:
    if hashlib.sha256(source).hexdigest() != ORIGINAL_SHA256:
        raise ValueError("unexpected vLLM-Omni serving source")
    if source.count(OLD.encode()) != 1 or source.count(FPS.encode()) != 1:
        raise ValueError("silent-output patch must match exactly once")
    result = source.replace(OLD.encode(), NEW.encode(), 1)
    result = result.replace(FPS.encode(), BOUNDED.encode(), 1)
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
    shutil.copyfile(
        Path(__file__).with_name("ccs_fasth3_controls.py"),
        root.parent / "ccs_fasth3_controls.py",
    )
    for cache in (path.parent / "__pycache__").glob("serving_video.*.pyc"):
        cache.unlink()


if __name__ == "__main__":
    main()
