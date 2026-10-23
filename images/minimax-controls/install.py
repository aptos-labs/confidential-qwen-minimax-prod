"""Install the reviewed silent-output patch without changing the watermark."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import stat
import tempfile
from importlib.metadata import distribution, version
from pathlib import Path
from typing import Any

ARTIFACT = Path(__file__).resolve().parent
TARGET = "entrypoints/openai/serving_video.py"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def regular_file(root: Path, name: str) -> Path:
    path = root / name
    if path.resolve() != path or not path.is_file():
        raise ValueError(f"missing or noncanonical package file: {name}")
    return path


def prepare(root: Path, artifact: Path = ARTIFACT) -> tuple[Path, bytes]:
    """Check source, patch and preserved watermark before allowing any write."""
    root = root.absolute()
    manifest: dict[str, Any] = json.loads((artifact / "manifest.json").read_text())
    if manifest["format"] != 1 or manifest["target"] != TARGET:
        raise ValueError("unexpected patch manifest")
    for name, expected in manifest["preserved_files"].items():
        if sha256(regular_file(root, name).read_bytes()) != expected:
            raise ValueError(f"preserved source drift: {name}")
    path = regular_file(root, TARGET)
    source = path.read_bytes()
    if sha256(source) == manifest["patched_sha256"]:
        return path, source
    if sha256(source) != manifest["original_sha256"]:
        raise ValueError("video server source drift")
    old, new = manifest["old"].encode(), manifest["new"].encode()
    if not old or source.count(old) != 1:
        raise ValueError("silent-output patch is not unique")
    patched = source.replace(old, new, 1)
    if sha256(patched) != manifest["patched_sha256"]:
        raise ValueError("silent-output patch hash mismatch")
    ast.parse(patched, filename=TARGET)
    return path, patched


def install(root: Path, artifact: Path = ARTIFACT) -> None:
    path, patched = prepare(root, artifact)
    if path.read_bytes() == patched:
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(patched)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    for cache in (path.parent / "__pycache__").glob("serving_video.*.pyc"):
        cache.unlink()
    path.with_suffix(".pyc").unlink(missing_ok=True)


def main() -> None:
    for name in ("vllm", "vllm-omni"):
        if version(name) != "0.28.0":
            raise ValueError(f"unsupported installed version: {name}")
    root = Path(str(distribution("vllm-omni").locate_file("vllm_omni"))).resolve()
    install(root)
    print("Installed silent output; existing watermark verified and preserved.")


if __name__ == "__main__":
    main()
