import asyncio
import base64
import io
import subprocess
import wave

import httpx
import pytest
from moderation_worker.app import Worker, create_app
from paid_gateway.moderation_contract import ALLOW, Verdict
from test_moderation import FakeGuard, FakeOmni, form
from test_moderation import decoder_pythonpath as decoder_pythonpath


def voice_request(count=1):
    wav = io.BytesIO()
    with wave.open(wav, "wb") as sample:
        sample.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        sample.writeframes(b"\x01\x00" * 48000 * 2)
    audio = (
        b'--test\r\nContent-Disposition: form-data; name="input_references"; '
        b'filename="voice.wav"\r\nContent-Type: audio/wav\r\n\r\n' + wav.getvalue() + b"\r\n"
    )
    return form(count=1, task="ref2va")[:-10] + audio * count + b"--test--\r\n"


@pytest.mark.parametrize("inputs", [False, True])
@pytest.mark.parametrize("audio_enabled", [False, True])
def test_audio_stage_independent_and_normalizes_full_samples(inputs, audio_enabled, monkeypatch):
    async def case():
        guard, omni = FakeGuard(), FakeOmni()
        if not audio_enabled:

            async def forbidden(*args):
                raise AssertionError("disabled audio moderation must not call the decoder")

            monkeypatch.setattr("moderation_worker.app.decode_voice", forbidden)
        worker = Worker(guard, omni)
        result = await worker.pre(
            voice_request(3),
            "multipart/form-data; boundary=test",
            inputs_enabled=inputs,
            audio_enabled=audio_enabled,
        )
        assert result == ALLOW
        assert len(guard.prompts) == int(inputs)
        assert len(omni.calls) == (2 if inputs else 0) + (3 if audio_enabled else 0)
        audio_calls = [
            part for call in omni.calls for part in call if part["type"] == "input_audio"
        ]
        assert len(audio_calls) == (3 if audio_enabled else 0)
        for part in audio_calls:
            with wave.open(
                io.BytesIO(base64.b64decode(part["input_audio"]["data"])), "rb"
            ) as sample:
                assert sample.getframerate() == 24000
                assert sample.getnchannels() == 2
                assert sample.getnframes() == 48000

    asyncio.run(case())


@pytest.mark.parametrize("fault", ["block", "error", "decoder"])
def test_enabled_audio_moderation_fails_closed(fault, monkeypatch):
    async def case():
        class AudioOmni(FakeOmni):
            async def classify(self, content):
                if fault == "error":
                    raise httpx.ReadTimeout("private voice sample")
                return Verdict("block", "sexual_content")

        if fault == "decoder":

            async def failed(*args):
                raise ValueError("private decoder error")

            monkeypatch.setattr("moderation_worker.app.decode_voice", failed)
        worker = Worker(FakeGuard(), AudioOmni())
        worker.ready = True
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(create_app(worker)), base_url="http://worker"
        ) as client:
            response = await client.post(
                "/pre",
                content=voice_request(),
                headers={
                    "Content-Type": "multipart/form-data; boundary=test",
                    "X-Moderation-Inputs": "false",
                    "X-Moderation-Audio": "true",
                },
            )
        assert response.status_code == (200 if fault == "block" else 503)
        assert response.json() == {
            "decision": "block",
            "category": ("sexual_content" if fault == "block" else "moderation_unavailable"),
        }
        assert not worker.busy and not worker.guard.prompts

    asyncio.run(case())


@pytest.mark.parametrize("value", ["", "0", "True", "false,true"])
def test_worker_rejects_ambiguous_audio_control(value):
    async def case():
        omni = FakeOmni()
        worker = Worker(FakeGuard(), omni)
        worker.ready = True
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(create_app(worker)), base_url="http://worker"
        ) as client:
            response = await client.post(
                "/pre",
                content=voice_request(),
                headers={
                    "Content-Type": "multipart/form-data; boundary=test",
                    "X-Moderation-Audio": value,
                },
            )
        assert response.status_code == 503 and not omni.calls

    asyncio.run(case())


@pytest.fixture(scope="module")
def mp3_samples(tmp_path_factory):
    directory = tmp_path_factory.mktemp("mp3-samples")
    result = {}
    for seconds in (1, 2, 3, 5, 8, 15, 16):
        path = directory / f"{seconds}.mp3"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=44100",
                "-t",
                str(seconds),
                "-c:a",
                "libmp3lame",
                str(path),
            ],
            check=True,
            timeout=30,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        result[seconds] = path.read_bytes()
    return result


def mp3_request(samples, *, wav_sample=False):
    body = voice_request()[:-10] if wav_sample else form(count=1, task="ref2va")[:-10]
    for sample in samples:
        body += (
            b'--test\r\nContent-Disposition: form-data; name="input_references"; '
            b'filename="voice.mp3"\r\nContent-Type: audio/mpeg\r\n\r\n' + sample + b"\r\n"
        )
    return body + b"--test--\r\n"


@pytest.mark.parametrize(
    "durations,valid",
    [
        ([2], True),
        ([15], True),
        ([5, 5, 5], True),
        ([8, 8], False),
        ([1], False),
        ([16], False),
    ],
)
def test_mp3_duration_checked_without_loading_or_calling_models(mp3_samples, durations, valid):
    async def case():
        guard, omni = FakeGuard(), FakeOmni()
        guard.healthy = False
        worker = Worker(guard, omni)
        assert not worker.ready
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(create_app(worker)), base_url="http://worker"
        ) as client:
            response = await client.post(
                "/pre",
                content=mp3_request([mp3_samples[d] for d in durations]),
                headers={
                    "Content-Type": "multipart/form-data; boundary=test",
                    "X-Moderation-Inputs": "false",
                    "X-Moderation-Audio": "false",
                },
            )
        assert response.status_code == (200 if valid else 400), response.text
        assert not guard.prompts and not omni.calls

    asyncio.run(case())


def test_mp3_and_wav_share_total_duration_limit(mp3_samples):
    async def case():
        worker = Worker(FakeGuard(), FakeOmni())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(create_app(worker)), base_url="http://worker"
        ) as client:
            response = await client.post(
                "/pre",
                content=mp3_request([mp3_samples[15]], wav_sample=True),
                headers={
                    "Content-Type": "multipart/form-data; boundary=test",
                    "X-Moderation-Inputs": "false",
                    "X-Moderation-Audio": "false",
                },
            )
        assert response.status_code == 400
        assert not worker.omni.calls

    asyncio.run(case())


def test_invalid_mp3_is_rejected_before_prompt_classification():
    async def case():
        worker = Worker(FakeGuard(), FakeOmni())
        worker.ready = True
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(create_app(worker)), base_url="http://worker"
        ) as client:
            response = await client.post(
                "/pre",
                content=mp3_request([b"ID3not a recording"]),
                headers={
                    "Content-Type": "multipart/form-data; boundary=test",
                },
            )
        assert response.status_code == 400
        assert response.json() == {"decision": "block", "category": "invalid_request"}
        assert not worker.guard.prompts and not worker.omni.calls

    asyncio.run(case())


def test_mp3_content_classification_is_optional_and_uses_normalized_audio(mp3_samples):
    async def case():
        omni = FakeOmni(Verdict("block", "sexual_content"))
        worker = Worker(FakeGuard(), omni)
        verdict = await worker.pre(
            mp3_request([mp3_samples[3]]),
            "multipart/form-data; boundary=test",
            inputs_enabled=False,
            audio_enabled=True,
        )
        assert verdict == Verdict("block", "sexual_content")
        assert not worker.guard.prompts and len(omni.calls) == 1
        raw = base64.b64decode(omni.calls[0][1]["input_audio"]["data"])
        with wave.open(io.BytesIO(raw), "rb") as sample:
            assert sample.getframerate() == 24000 and sample.getnframes() == 72000

    asyncio.run(case())
