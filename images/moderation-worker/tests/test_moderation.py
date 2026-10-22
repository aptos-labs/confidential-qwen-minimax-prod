import asyncio
import io
import json
import os
import subprocess
from pathlib import Path

import httpx
import pytest
from moderation_worker.app import Worker, create_app
from moderation_worker.media import checked_image, decode_video, run_decoder
from moderation_worker.models import Omni
from moderation_worker.policy import GUARD_CATEGORIES, guard_verdict
from paid_gateway.moderation_contract import ALLOW, UNAVAILABLE, ModerationFailure, Verdict
from PIL import Image


def form(prompt="A red square", negative="", count=0):
    fields = {
        "model": "minimax-h3-fl2va",
        "prompt": prompt,
        "negative_prompt": negative,
        "extra_params": json.dumps({"task": "fl2va" if count else "t2va", "duration": 4}),
    }
    result = b""
    for name, value in fields.items():
        result += (
            f'--test\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    for _ in range(count):
        result += (
            b'--test\r\nContent-Disposition: form-data; name="input_references"; '
            b'filename="frame.png"\r\nContent-Type: image/png\r\n\r\n' + png() + b"\r\n"
        )
    return result + b"--test--\r\n"


def png():
    output = io.BytesIO()
    Image.new("RGB", (32, 32), "red").save(output, "PNG")
    return output.getvalue()


@pytest.mark.parametrize("severity", ["Unsafe", "Controversial"])
@pytest.mark.parametrize("category", GUARD_CATEGORIES)
def test_guard_never_allows_non_safe_severity(severity, category):
    assert guard_verdict(f"Safety: {severity}\nCategories: {category}").decision == "block"


@pytest.mark.parametrize("severity", ["Safe", "Unsafe", "Controversial"])
def test_sexual_override(severity):
    assert guard_verdict(
        f"Safety: {severity}\nCategories: Sexual Content or Sexual Acts"
    ) == Verdict("block", "sexual_content")


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "safe",
        "Safety: Safe",
        "Safety: Safe\nCategories: Violent",
        "Safety: Safe\nCategories: None\nExplanation: private",
        "Safety: Safe\nCategories: None, Violent",
        "Safety: Unsafe\nCategories: Imaginary",
        "Safety: Safe\nCategories: None, None",
    ],
)
def test_guard_bad_classifications_are_failures(raw):
    with pytest.raises(ModerationFailure):
        guard_verdict(raw)


def test_guard_only_valid_safe_can_allow():
    assert guard_verdict("Safety: Safe\nCategories: None") == ALLOW


@pytest.mark.parametrize(
    "raw",
    [
        '{"decision":"allow","category":"sexual_content"}',
        '{"decision":"block","category":"none"}',
        '{"decision":"allow","category":"none","explanation":"private"}',
        '{"decision":"block","decision":"allow","category":"none"}',
        '{"decision":"allow","category":null}',
        "[]",
        "{}",
        "null",
        '{"decision":"allow","category":NaN}',
    ],
)
def test_wire_rejects_ambiguous_verdicts(raw):
    with pytest.raises(ModerationFailure):
        Verdict.parse(raw)


def response(value=None, reason="stop"):
    return {
        "choices": [
            {
                "finish_reason": reason,
                "message": {
                    "role": "assistant",
                    "content": json.dumps(value or ALLOW.wire()),
                },
            }
        ]
    }


@pytest.mark.parametrize(
    "fault",
    [
        "length",
        "missing",
        "duplicate",
        "tool",
        "refusal",
        "invalid",
        "large",
        "error",
        "redirect",
        "timeout",
    ],
)
def test_omni_failure_never_allows(fault):
    async def case():
        def handler(request):
            assert request.url.host == "vllm-omni-qwen"
            assert "authorization" not in request.headers
            payload = json.loads(request.content)
            assert payload["modalities"] == ["text"] and payload["temperature"] == 0
            data = response()
            if fault == "length":
                data["choices"][0]["finish_reason"] = "length"
            elif fault == "missing":
                data["choices"] = []
            elif fault == "duplicate":
                data["choices"] *= 2
            elif fault in {"tool", "refusal"}:
                data["choices"][0]["message"]["tool_calls" if fault == "tool" else "refusal"] = (
                    "private"
                )
            elif fault == "invalid":
                data["choices"][0]["message"]["content"] = "I cannot help with that"
            elif fault == "large":
                return httpx.Response(
                    200, content=b"x" * 32769, headers={"content-type": "application/json"}
                )
            elif fault == "error":
                return httpx.Response(500)
            elif fault == "redirect":
                return httpx.Response(302, headers={"location": "https://outside.invalid"})
            elif fault == "timeout":
                raise httpx.ReadTimeout("private")
            return httpx.Response(200, json=data)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ModerationFailure):
                await Omni(client).classify([{"type": "text", "text": "private"}])

    asyncio.run(case())


class FakeGuard:
    healthy = True

    def __init__(self, verdict=ALLOW):
        self.verdict = verdict
        self.prompts = []

    async def check(self, prompt):
        self.prompts.append(prompt)
        return self.verdict


class FakeOmni:
    def __init__(self, verdict=ALLOW):
        self.verdict = verdict
        self.calls = []

    async def classify(self, content):
        self.calls.append(content)
        return self.verdict

    async def close(self):
        pass


@pytest.mark.parametrize("count", [0, 1, 2])
def test_prechecks_every_upload_and_preserves_negative_prompt_role(count):
    async def case():
        guard, omni = FakeGuard(), FakeOmni()
        result = await Worker(guard, omni).pre(
            form(negative="no nudity", count=count), "multipart/form-data; boundary=test"
        )
        assert result == ALLOW
        assert guard.prompts == ["A red square"]
        assert len(omni.calls) == count + 1
        assert json.loads(omni.calls[0][0]["text"])["negative_prompt"] == "no nudity"
        assert all(c[1]["type"] == "image_url" for c in omni.calls[1:])

    asyncio.run(case())


@pytest.mark.parametrize("category", ["guard_controversial", "guard_unsafe", "sexual_content"])
def test_omni_cannot_override_guard_block(category):
    async def case():
        blocked = Verdict("block", category)
        omni = FakeOmni()
        assert (
            await Worker(FakeGuard(blocked), omni).pre(
                form(), "multipart/form-data; boundary=test"
            )
            == blocked
        )
        assert not omni.calls

    asyncio.run(case())


@pytest.mark.parametrize("state", ["not_ready", "busy", "unhealthy", "exception", "timeout"])
def test_worker_failures_are_fixed_blocks_without_content(state, caplog):
    async def case():
        guard, omni = FakeGuard(), FakeOmni()
        worker = Worker(guard, omni)
        worker.ready = state != "not_ready"
        worker.busy = state == "busy"
        guard.healthy = state != "unhealthy"
        if state in {"exception", "timeout"}:

            async def fail(*args):
                raise TimeoutError("private") if state == "timeout" else RuntimeError("private")

            guard.check = fail
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(create_app(worker)), base_url="http://test"
        ) as client:
            result = await client.post(
                "/pre",
                content=form("private"),
                headers={"content-type": "multipart/form-data; boundary=test"},
            )
            assert result.status_code == 503 and result.json() == UNAVAILABLE.wire()
            assert "private" not in result.text + caplog.text

    asyncio.run(case())


@pytest.mark.parametrize("state", ["not_ready", "unhealthy"])
def test_boot_liveness_does_not_enable_video_checks(state):
    async def case():
        guard, omni = FakeGuard(), FakeOmni()
        worker = Worker(guard, omni)
        worker.ready = state != "not_ready"
        guard.healthy = state != "unhealthy"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(create_app(worker)), base_url="http://test"
        ) as client:
            assert (await client.get("/health/liveliness")).status_code == 200
            assert (await client.get("/health/readiness")).status_code == 503
            for path in ("/pre", "/post"):
                result = await client.post(path, content=b"fixture")
                assert result.status_code == 503 and result.json() == UNAVAILABLE.wire()
            assert not guard.prompts and not omni.calls

    asyncio.run(case())


def test_image_full_decode_and_bounds(monkeypatch):
    assert checked_image(png()).startswith(b"\x89PNG")
    for data in (b"\x89PNG\r\n\x1a\nfixture", png()[:40]):
        with pytest.raises(ModerationFailure):
            checked_image(data)
    monkeypatch.setattr("moderation_worker.media.MAX_IMAGE_PIXELS", 100)
    with pytest.raises(ModerationFailure):
        checked_image(png())


@pytest.fixture(scope="session")
def videos(tmp_path_factory):
    directory = tmp_path_factory.mktemp("videos")
    result = {}
    for name, duration, audio in [("silent", "4", False), ("audio", "4.2", True)]:
        path = directory / f"{name}.mp4"
        args = ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=red:s=64x64:r=24"]
        if audio:
            args += [
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=32000",
                "-c:a",
                "aac",
                "-ac",
                "2",
            ]
        args += [
            "-t",
            duration,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(path),
        ]
        subprocess.run(
            args, check=True, timeout=30, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        result[name] = path.read_bytes()
    return result


@pytest.fixture(autouse=True)
def decoder_pythonpath(monkeypatch):
    import moderation_worker
    import paid_gateway

    monkeypatch.setenv(
        "PYTHONPATH",
        os.pathsep.join(
            str(Path(m.__file__).resolve().parent.parent)
            for m in (moderation_worker, paid_gateway)
        ),
    )


@pytest.mark.parametrize("name,count,has_audio", [("silent", 4, False), ("audio", 5, True)])
def test_real_decoder_covers_partial_second_and_entire_audio(
    videos, tmp_path, name, count, has_audio
):
    async def case():
        result = await decode_video(videos[name], tmp_path, audio_expected=has_audio)
        assert len(result.frames) == count
        assert (result.audio is not None) == has_audio
        for frame in result.frames:
            checked_image(frame.read_bytes())

    asyncio.run(case())


def test_unexpected_missing_audio_blocks(videos, tmp_path):
    with pytest.raises(ModerationFailure):
        asyncio.run(decode_video(videos["silent"], tmp_path, audio_expected=True))


def test_postchecks_every_frame_and_audio_then_cleans_scratch(videos, tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))

    async def case():
        omni = FakeOmni()
        assert await Worker(FakeGuard(), omni).post(videos["audio"], True) == ALLOW
        assert len(omni.calls) == 6
        assert all(c[1]["type"] == "image_url" for c in omni.calls[:5])
        assert omni.calls[-1][1]["type"] == "input_audio"
        assert not list(tmp_path.iterdir())

    asyncio.run(case())


def test_post_block_discards_video_and_cleans_scratch(videos, tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))

    async def case():
        omni = FakeOmni(Verdict("block", "sexual_content"))
        assert (await Worker(FakeGuard(), omni).post(videos["audio"], True)).decision == "block"
        assert len(omni.calls) == 1 and not list(tmp_path.iterdir())

    asyncio.run(case())


def test_decoder_cancellation_reaps_process():
    async def case():
        task = asyncio.create_task(run_decoder("python3", ["-c", "import time; time.sleep(30)"]))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(case())


@pytest.mark.parametrize("blocked_call", [5, 6])
def test_last_frame_or_audio_can_block_output(videos, blocked_call):
    async def case():
        class LaterBlock(FakeOmni):
            async def classify(self, content):
                self.calls.append(content)
                return (
                    Verdict("block", "sexual_content")
                    if len(self.calls) == blocked_call
                    else ALLOW
                )

        omni = LaterBlock()
        assert await Worker(FakeGuard(), omni).post(videos["audio"], True) == Verdict(
            "block", "sexual_content"
        )
        assert len(omni.calls) == blocked_call

    asyncio.run(case())


def test_worker_disconnect_cancels_check_and_releases_capacity():
    async def case():
        started, cancelled = asyncio.Event(), asyncio.Event()

        class SlowGuard(FakeGuard):
            async def check(self, prompt):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        worker = Worker(SlowGuard(), FakeOmni())
        worker.ready = True
        first = True

        async def receive():
            nonlocal first
            if first:
                first = False
                return {"type": "http.request", "body": form(), "more_body": False}
            await started.wait()
            return {"type": "http.disconnect"}

        events = []

        async def send(event):
            events.append(event)

        await create_app(worker)(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/pre",
                "query_string": b"",
                "headers": [(b"content-type", b"multipart/form-data; boundary=test")],
                "server": ("test", 8090),
                "client": ("test", 1),
            },
            receive,
            send,
        )
        assert cancelled.is_set() and not worker.busy
        assert not any(b'"decision":"allow"' in e.get("body", b"") for e in events)

    asyncio.run(case())


def test_duplicate_multipart_parameter_rejected():
    from paid_gateway.client import GatewayError
    from paid_gateway.video_request import parse_video

    body = form().replace(b'name="prompt"', b'name="prompt"; name="negative_prompt"')
    with pytest.raises(GatewayError):
        parse_video(body, "multipart/form-data; boundary=test")


@pytest.mark.parametrize("stage", ["cuda_availability", "weights_load", "guard_generate"])
def test_startup_failure_retains_only_fixed_stage(stage):
    async def case():
        class StartupGuard(FakeGuard):
            def load(self):
                self.startup_stage = stage
                if stage != "guard_generate":
                    raise RuntimeError("private detail")

            async def check(self, prompt):
                raise RuntimeError("private detail")

        worker = Worker(StartupGuard(), FakeOmni())
        await worker.initialize()
        assert worker.startup_health() == {"status": "unavailable", "stage": stage, "failed": True}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(create_app(worker)), base_url="http://test"
        ) as client:
            health = await client.get("/health/startup")
            assert health.status_code == 503
            assert "private" not in health.text
            assert (await client.get("/health/readiness")).status_code == 503
            for path in ("/pre", "/post"):
                assert (await client.post(path, content=b"fixture")).json() == UNAVAILABLE.wire()

    asyncio.run(case())
