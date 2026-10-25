"""Bounded, content-free failure locations for the temporary FastH3 trial.

Never serialize exceptions, log records, source lines, locals, or request IDs.
Only build-time file IDs, line numbers and fixed enumerations leave the process.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

DIRECTORY = Path("/var/cache/fasth3/diagnostics")
MANIFEST = Path(__file__).with_name("ccs_fasth3_locations.json")
SITES = frozenset({"runner", "worker", "executor", "pipeline", "vsa", "api"})
KINDS = frozenset(
    {
        "RuntimeError",
        "ValueError",
        "TypeError",
        "AssertionError",
        "KeyError",
        "AttributeError",
        "ImportError",
        "OSError",
        "TimeoutError",
        "MemoryError",
        "OutOfMemoryError",
        "AcceleratorError",
        "CompilationError",
        "OutOfResources",
        "EngineGenerateError",
        "EngineDeadError",
        "other",
    }
)
REASONS = {
    "cuda_oom": "CUDA out of memory",
    "illegal_memory_access": "illegal memory access",
    "illegal_instruction": "illegal instruction",
    "device_assert": "device-side assert",
    "invalid_kernel_image": "no kernel image is available",
    "invalid_device_function": "invalid device function",
    "kernel_resources": "too many resources requested",
    "read_only_filesystem": "Read-only file system",
    "permission_denied": "Permission denied",
    "nccl": "NCCL error",
}
_locations: dict[str, list[int]] | None = None


def capture(site: str, exc: BaseException | None) -> None:
    """Best effort: diagnostic I/O must never replace the original exception."""
    global _locations
    if site not in SITES or exc is None:
        return
    try:
        if _locations is None:
            _locations = json.loads(MANIFEST.read_text())
        assert _locations is not None
        kind = next((cls.__name__ for cls in type(exc).__mro__ if cls.__name__ in KINDS), "other")
        # Inspect only to select a fixed code; never persist arbitrary text.
        message = str(exc)
        reason = next((key for key, needle in REASONS.items() if needle in message), "unknown")
        frames: list[list[int]] = []
        tb = exc.__traceback__
        while tb is not None:
            location = _locations.get(tb.tb_frame.f_code.co_filename)
            if location is not None and 0 < tb.tb_lineno <= location[1]:
                frames.append([location[0], tb.tb_lineno])
            tb = tb.tb_next
        record = {"kind": kind, "reason": reason, "frames": frames[-12:]}
        DIRECTORY.mkdir(mode=0o700, exist_ok=True)
        path = DIRECTORY / f"{os.getpid()}-{site}.json"
        temp = path.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(record, output)
        os.replace(temp, path)
    except Exception:
        pass


def metrics() -> bytes:
    """Expose only validated fixed labels and bounded integer locations."""
    lines = [
        "# HELP ccs_fasth3_failure_location Last captured exception location per process and boundary.",
        "# TYPE ccs_fasth3_failure_location gauge",
    ]
    names: dict[int, str] = {}
    try:
        for filename, (index, _length) in json.loads(MANIFEST.read_text()).items():
            relative = str(Path(filename).relative_to(MANIFEST.parent))
            if re.fullmatch(
                r"(?:vllm_omni|vllm|torch|triton|fastvideo_kernel)/[A-Za-z0-9_/]+\.py", relative
            ):
                names[index] = relative
    except (OSError, ValueError, TypeError):
        pass
    for path in sorted(DIRECTORY.glob("*.json"))[:64]:
        try:
            pid, site = path.stem.split("-", 1)
            if not pid.isdecimal() or len(pid) > 10 or site not in SITES:
                continue
            if path.is_symlink() or path.stat().st_size > 4096:
                continue
            record = json.loads(path.read_text())
            kind, reason = record["kind"], record["reason"]
            if kind not in KINDS or reason not in {*REASONS, "unknown"}:
                continue
            frames = record["frames"]
            if not isinstance(frames, list) or len(frames) > 12:
                continue
            for index, pair in enumerate(frames or [[0, 0]]):
                if not isinstance(pair, list) or len(pair) != 2:
                    continue
                if any(type(n) is not int or not 0 <= n <= 1000000 for n in pair):
                    continue
                lines.append(
                    f'ccs_fasth3_failure_location{{site="{site}",kind="{kind}",reason="{reason}",'
                    f'process="{pid}",frame="{index}",file="{pair[0]}",'
                    f'code="{names.get(pair[0], "unknown")}",line="{pair[1]}"}} 1'
                )
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return ("\n" + "\n".join(lines) + "\n").encode()


class DiagnosticsMiddleware:
    """Append diagnostics to existing metrics; preserve all other responses."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["path"] != "/metrics":
            await self.inner(scope, receive, send)
            return
        scope = dict(scope)
        scope["headers"] = [
            (key, value)
            for key, value in scope.get("headers", [])
            if key.lower() not in {b"accept-encoding", b"accept"}
        ] + [(b"accept", b"text/plain; version=0.0.4")]
        append = False

        async def send_metrics(message: Any) -> None:
            nonlocal append
            if message["type"] == "http.response.start":
                append = message["status"] == 200
                if append:
                    message = dict(message)
                    message["headers"] = [
                        (key, value)
                        for key, value in message.get("headers", [])
                        if key.lower() != b"content-length"
                    ]
            elif (
                append and message["type"] == "http.response.body" and not message.get("more_body")
            ):
                message = dict(message)
                message["body"] = message.get("body", b"") + metrics()
            await send(message)

        await self.inner(scope, receive, send_metrics)
