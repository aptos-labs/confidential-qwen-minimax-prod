"""Install reviewed silent-output and operator watermark controls."""

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
WATERMARK_TARGET = "diffusion/utils/commercial_use.py"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def regular_file(root: Path, name: str) -> Path:
    path = root / name
    if path.resolve() != path or not path.is_file():
        raise ValueError(f"missing or noncanonical package file: {name}")
    return path


def prepare(root: Path, artifact: Path = ARTIFACT) -> list[tuple[Path, bytes]]:
    """Check every source and patch before allowing any write."""
    root = root.absolute()
    manifest: dict[str, Any] = json.loads((artifact / "manifest.json").read_text())
    if manifest["format"] != 2 or set(manifest["patches"]) != {TARGET, WATERMARK_TARGET}:
        raise ValueError("unexpected patch manifest")
    for name, expected in manifest["preserved_files"].items():
        if sha256(regular_file(root, name).read_bytes()) != expected:
            raise ValueError(f"preserved source drift: {name}")
    prepared = []
    for name, patch in manifest["patches"].items():
        path = regular_file(root, name)
        source = path.read_bytes()
        if sha256(source) == patch["patched_sha256"]:
            prepared.append((path, source))
            continue
        if sha256(source) != patch["original_sha256"]:
            raise ValueError(f"source drift: {name}")
        patched = source
        for edit in patch["edits"]:
            old, new = edit["old"].encode(), edit["new"].encode()
            if not old or patched.count(old) != 1:
                raise ValueError(f"patch is not unique: {name}")
            patched = patched.replace(old, new, 1)
        if sha256(patched) != patch["patched_sha256"]:
            raise ValueError(f"patch hash mismatch: {name}")
        ast.parse(patched, filename=name)
        prepared.append((path, patched))
    return prepared


def install(root: Path, artifact: Path = ARTIFACT) -> None:
    for path, patched in prepare(root, artifact):
        replace_file(path, patched)


def replace_file(path: Path, patched: bytes) -> None:
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
    for cache in (path.parent / "__pycache__").glob(f"{path.stem}.*.pyc"):
        cache.unlink()
    path.with_suffix(".pyc").unlink(missing_ok=True)


def main() -> None:
    for name in ("vllm", "vllm-omni"):
        if version(name) != "0.28.0":
            raise ValueError(f"unsupported installed version: {name}")
    root = Path(str(distribution("vllm-omni").locate_file("vllm_omni"))).resolve()
    install(root)
    print("Installed silent output and operator watermark control.")


if __name__ == "__main__":
    main()
