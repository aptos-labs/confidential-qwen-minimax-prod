import ast
import asyncio
import hashlib
import json
import os
import sys
from unittest.mock import AsyncMock

import pytest
from conftest import diagnostic_installer, diagnostics


@pytest.fixture
def private_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "DIRECTORY", tmp_path / "private")
    monkeypatch.setattr(diagnostics, "MANIFEST", tmp_path / "locations.json")
    monkeypatch.setattr(diagnostics, "_locations", None)
    return tmp_path


def test_exception_payload_locals_and_unknown_paths_never_leave_process(private_diagnostics):
    filename = private_diagnostics / "torch" / "kernel.py"
    diagnostics.MANIFEST.write_text(json.dumps({str(filename): [7, 50]}))
    namespace = {"capture": diagnostics.capture}
    code = (
        'secret = "private-prompt-and-media"\n'
        "try:\n"
        '    raise RuntimeError("CUDA out of memory " + secret)\n'
        "except RuntimeError as exc:\n"
        '    capture("runner", exc)\n'
    )
    exec(compile(code, str(filename), "exec"), namespace)
    record = next(diagnostics.DIRECTORY.glob("*.json"))
    assert record.stat().st_mode & 0o077 == 0
    assert json.loads(record.read_text()) == {
        "kind": "RuntimeError",
        "reason": "cuda_oom",
        "frames": [[7, 3]],
    }
    metrics = diagnostics.metrics().decode()
    assert 'code="torch/kernel.py",line="3"' in metrics
    assert 'reason="cuda_oom"' in metrics
    assert "private-prompt-and-media" not in metrics + record.read_text()
    assert str(private_diagnostics) not in metrics
    exec(compile(code, "private-generated-filename", "exec"), namespace)
    assert json.loads(record.read_text())["frames"] == []
    assert "private-generated-filename" not in diagnostics.metrics().decode()


def test_diagnostic_io_failure_preserves_original_exception(private_diagnostics):
    diagnostics.MANIFEST.write_text("invalid")
    exc = RuntimeError("private text")
    diagnostics.capture("runner", exc)
    assert not diagnostics.DIRECTORY.exists()


def test_metrics_reject_untrusted_labels_and_oversized_files(private_diagnostics):
    diagnostics.MANIFEST.write_text("{}")
    diagnostics.DIRECTORY.mkdir()
    valid = {"kind": "ValueError", "reason": "unknown", "frames": [[2, 14]]}
    for name, record in (
        ("1-api", {**valid, "kind": "private-prompt"}),
        ("2-api", {**valid, "reason": "private-prompt"}),
        ("3-api", {**valid, "frames": [["private-prompt", 4]]}),
        ("4-private-prompt", valid),
    ):
        (diagnostics.DIRECTORY / (name + ".json")).write_text(json.dumps(record))
    (diagnostics.DIRECTORY / "5-api.json").write_text("private-prompt" * 500)
    assert "private-prompt" not in diagnostics.metrics().decode()
    assert "} 1" not in diagnostics.metrics().decode()


@pytest.mark.parametrize("path,status", [("/metrics", 200), ("/metrics", 503), ("/health", 200)])
def test_asgi_metrics_preserve_other_responses(private_diagnostics, path, status):
    diagnostics.MANIFEST.write_text("{}")
    response = AsyncMock()

    async def app(scope, receive, send):
        if path == "/metrics":
            assert b"accept-encoding" not in dict(scope["headers"])
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-length", b"4")],
            }
        )
        await send({"type": "http.response.body", "body": b"up ", "more_body": True})
        await send({"type": "http.response.body", "body": b"1"})

    asyncio.run(
        diagnostics.DiagnosticsMiddleware(app)(
            {"type": "http", "path": path, "headers": [(b"accept-encoding", b"gzip")]},
            AsyncMock(),
            response,
        )
    )
    messages = [call.args[0] for call in response.await_args_list]
    if path == "/metrics" and status == 200:
        assert messages[0]["headers"] == []
        assert messages[2]["body"].startswith(b"1\n# HELP ccs_fasth3_failure_location")
    else:
        assert messages[0]["headers"] == [(b"content-length", b"4")]
        assert messages[2]["body"] == b"1"
    assert messages[1]["body"] == b"up "


def test_real_upstream_handlers_capture_before_cleanup(upstream):
    for name, (site, digest) in diagnostic_installer.TARGETS.items():
        source = (upstream / name).read_bytes()
        result = diagnostic_installer.patched(source, site=site, expected=digest)
        ast.parse(result)
        handlers = [n for n in ast.walk(ast.parse(result)) if isinstance(n, ast.ExceptHandler)]
        assert handlers
        assert all(ast.unparse(node.body[0]).startswith("_ccs_capture(") for node in handlers)
        if site == "worker":
            assert b'_ccs_capture("worker", exc)\n    exc.__traceback__ = None' in result
        with pytest.raises(ValueError, match="unexpected"):
            diagnostic_installer.patched(source + b"\n", site=site, expected=digest)


def test_instrumented_module_imports_and_propagates_original_error(private_diagnostics):
    source = b'import math\ntry:\n    raise RuntimeError("private-prompt")\nexcept RuntimeError:\n    raise\n'
    result = diagnostic_installer.patched(
        source, site="runner", expected=hashlib.sha256(source).hexdigest()
    )
    diagnostics.MANIFEST.write_text(json.dumps({"pinned-module": [8, 100]}))
    with pytest.raises(RuntimeError, match="private-prompt"):
        exec(compile(result, "pinned-module", "exec"), {})
    record = diagnostics.DIRECTORY / f"{os.getpid()}-runner.json"
    assert json.loads(record.read_text())["kind"] == "RuntimeError"
    assert sys.modules["ccs_fasth3_diagnostics"] is diagnostics
