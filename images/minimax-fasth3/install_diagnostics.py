"""Instrument only exact, pinned upstream sources for the temporary trial."""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
from pathlib import Path

# Filled with the reviewed v0.30.0 source hashes; any source drift fails the build.
TARGETS = {
    "diffusion/worker/diffusion_model_runner.py": (
        "runner",
        "ada7e5876b814c41fb40e1f5fcba81de170d3427f5c1a2066a81fba4196a3fe0",
    ),
    "diffusion/worker/diffusion_worker.py": (
        "worker",
        "48e36974d51640e05017add752f56c3789c574671e715198a88f1ff784106f6f",
    ),
    "diffusion/executor/multiproc_executor.py": (
        "executor",
        "e94c47cbfd9486b1d8b30aa93797cb135acaea69d44f66cc2626778d2d22050e",
    ),
    "diffusion/models/minimax_h3/pipeline_minimax_h3.py": (
        "pipeline",
        "fda01a7ced807952bf80c3f9370d9dc0b4d77f960485b00de883abd974c952f0",
    ),
    "diffusion/models/minimax_h3/attention/fastvideo_h3.py": (
        "vsa",
        "f21648d3ba5b0bad4f5e083f19048dbc54dd2ab29043af6f776e6933d392338d",
    ),
    "entrypoints/openai/api_server.py": (
        "api",
        "86a6f4f9c677ba3ea4ae7feda1ede072381f31bf0044f4e86baee8931ae7fc04",
    ),
}


def patched(source: bytes, *, site: str, expected: str) -> bytes:
    if hashlib.sha256(source).hexdigest() != expected:
        raise ValueError("unexpected diagnostics source")
    lines = source.decode().splitlines(keepends=True)
    tree = ast.parse(source)
    handlers = [node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)]
    for node in sorted(handlers, key=lambda n: n.body[0].lineno, reverse=True):
        first = node.body[0]
        indent = " " * first.col_offset
        lines.insert(
            first.lineno - 1,
            indent + f'_ccs_capture("{site}", _ccs_sys.exception())\n',
        )
    first_import = next(
        node
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and not (isinstance(node, ast.ImportFrom) and node.module == "__future__")
    )
    lines.insert(
        first_import.lineno - 1,
        "import sys as _ccs_sys\nfrom ccs_fasth3_diagnostics import capture as _ccs_capture\n",
    )
    result = "".join(lines)
    if site == "worker":
        anchor = "    exc.__traceback__ = None\n"
        if result.count(anchor) != 1:
            raise ValueError("cleanup capture must match once")
        result = result.replace(anchor, '    _ccs_capture("worker", exc)\n' + anchor, 1)
    if site == "api":
        anchor = "            _TimestampMiddleware(app),\n"
        if result.count(anchor) != 1:
            raise ValueError("metrics wrapper must match once")
        result = result.replace(
            anchor,
            "            _TimestampMiddleware(__import__('ccs_fasth3_diagnostics').DiagnosticsMiddleware(app)),\n",
            1,
        )
    ast.parse(result)
    return result.encode()


def install(root: Path) -> None:
    for name, (site, expected) in TARGETS.items():
        path = root / name
        path.write_bytes(patched(path.read_bytes(), site=site, expected=expected))
        for cache in (path.parent / "__pycache__").glob(path.stem + ".*.pyc"):
            cache.unlink()
    shutil.copyfile(
        Path(__file__).with_name("ccs_fasth3_diagnostics.py"),
        root.parent / "ccs_fasth3_diagnostics.py",
    )
    # IDs map to immutable image code, never filenames supplied by requests/JIT.
    sources = sorted(
        path.resolve()
        for package in ("vllm_omni", "vllm", "torch", "triton", "fastvideo_kernel")
        for path in (root.parent / package).rglob("*.py")
    )
    locations = {
        str(path): [index, len(path.read_bytes().splitlines())]
        for index, path in enumerate(sources, start=1)
    }
    (root.parent / "ccs_fasth3_locations.json").write_text(json.dumps(locations))
