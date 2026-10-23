"""Voice inputs remain bounded even when their optional content classifier is disabled."""

import asyncio
import io
import json
import struct
import wave
from fractions import Fraction

import httpx
import pytest
from paid_gateway.app import PaidGateway
from paid_gateway.client import GatewayError
from paid_gateway.moderation import ModerationClient, ModerationConfig
from paid_gateway.moderation_contract import ALLOW
from paid_gateway.video_request import parse_video
from paid_gateway.voice_sample import voice_duration
from test_gateway import KEY, FakeBilling, downstream, form

CONTENT_TYPE = "multipart/form-data; boundary=fixture-boundary"


def wav(seconds=2, rate=24000, channels=1, width=2):
    output = io.BytesIO()
    with wave.open(output, "wb") as sample:
        sample.setparams((channels, width, rate, 0, "NONE", "not compressed"))
        sample.writeframes(b"\x01" * int(seconds * rate) * channels * width)
    return output.getvalue()


def voice_form(audios=(), *, images=1, task="ref2va", mime="audio/wav", extra=()):
    body = form(
        [
            ("model", "minimax-h3-fl2va"),
            ("prompt", "<Subject 1> uses the voice in <Audio 1>."),
            ("extra_params", json.dumps({"task": task, "duration": 5})),
            *extra,
        ],
        [("input_references", b"\x89PNG\r\n\x1a\nfixture") for _ in range(images)],
    )
    marker = b"--fixture-boundary"
    parts = [body[: -len(marker + b"--\r\n")]]
    for audio in audios:
        filename = b"voice.mp3" if mime == "audio/mpeg" else b"voice.wav"
        parts.append(
            marker + b'\r\nContent-Disposition: form-data; name="input_references"; '
            b'filename="'
            + filename
            + b'"\r\nContent-Type: '
            + mime.encode()
            + b"\r\n\r\n"
            + audio
            + b"\r\n"
        )
    return b"".join(parts) + marker + b"--\r\n"


@pytest.mark.parametrize("rate", [16000, 22050, 24000, 32000, 44100, 48000])
@pytest.mark.parametrize("channels", [1, 2])
def test_pcm_sample_validated_without_gpu_or_decoder(rate, channels):
    assert voice_duration(wav(2, rate, channels)) == 2


@pytest.mark.parametrize(
    "audio",
    [
        b"not audio",
        wav()[:-1],
        wav() + b"extra",
        wav(1.99),
        wav(15.01),
        wav(rate=8000),
        wav(channels=3),
        wav(width=1),
        wav(width=3),
    ],
)
def test_malformed_or_out_of_bounds_voice_rejected(audio):
    with pytest.raises(GatewayError) as error:
        parse_video(voice_form([audio]), CONTENT_TYPE)
    assert error.value.code == "invalid_request"


def test_reject_duplicate_audio_chunks_and_partial_pcm_frame():
    for append in (b"data\x00\x00\x00\x00", b"x"):
        audio = bytearray(wav()) + append
        struct.pack_into("<I", audio, 4, len(audio) - 8)
        if append == b"x":
            struct.pack_into("<I", audio, 40, len(audio) - 44)
        with pytest.raises(GatewayError):
            voice_duration(bytes(audio))


@pytest.mark.parametrize(
    "durations,images,task,allowed",
    [
        ([2], 1, "ref2va", True),
        ([5, 5, 5], 9, "ref2va", True),
        ([15], 1, "ref2va", True),
        ([8, 8], 1, "ref2va", False),
        ([2, 2, 2, 2], 1, "ref2va", False),
        ([2], 10, "ref2va", False),
        ([2], 0, "ref2va", False),
        ([2], 1, "fl2va", False),
        ([2], 1, "t2va", False),
    ],
)
def test_mixed_reference_limits(durations, images, task, allowed):
    body = voice_form([wav(duration) for duration in durations], images=images, task=task)
    if not allowed:
        with pytest.raises(GatewayError):
            parse_video(body, CONTENT_TYPE)
        return
    parsed = parse_video(body, CONTENT_TYPE)
    assert parsed.modalities == ["text", "image", "audio"]
    assert len(parsed.audios) == len(durations)
    assert len(parsed.images) == images


@pytest.mark.parametrize("mime", ["audio/ogg", "application/octet-stream"])
def test_non_pcm_mime_rejected(mime):
    with pytest.raises(GatewayError):
        parse_video(voice_form([wav()], mime=mime), CONTENT_TYPE)


@pytest.mark.parametrize(
    "extra",
    [
        ("audio_reference", '{"audio_url":"https://outside.invalid/voice.wav"}'),
        ("audio_moderation", "false"),
        ("CCS_MODERATION_AUDIO_ENABLED", "false"),
    ],
)
def test_no_remote_audio_or_per_request_moderation_override(extra):
    with pytest.raises(GatewayError):
        parse_video(voice_form([wav()], extra=[extra]), CONTENT_TYPE)


@pytest.mark.parametrize("inputs", [False, True])
@pytest.mark.parametrize("audio_enabled", [False, True])
@pytest.mark.parametrize("failure", [None, "block", "error"])
def test_voice_forwarding_and_independent_moderation(inputs, audio_enabled, failure, monkeypatch):
    async def case():
        sent, checked = [], []
        body = voice_form([wav(), wav(3)])

        async def classifier(request):
            checked.append(request)
            assert request.content == body
            assert request.headers["x-moderation-inputs"] == str(inputs).lower()
            assert request.headers["x-moderation-audio"] == str(audio_enabled).lower()
            if failure == "error":
                raise httpx.ReadTimeout("private audio")
            return httpx.Response(
                200,
                json=(
                    {"decision": "block", "category": "sexual_content"}
                    if failure == "block"
                    else ALLOW.wire()
                ),
            )

        async def model(request):
            sent.append(request)
            assert request.content == body  # Original mixed multipart bytes reach H3 unchanged.
            return httpx.Response(200, content=b"video", headers={"content-type": "video/mp4"})

        billing = FakeBilling("absent")
        gateway = PaidGateway(
            downstream,
            billing,
            httpx.AsyncClient(transport=httpx.MockTransport(model)),
            ModerationClient(httpx.AsyncClient(transport=httpx.MockTransport(classifier))),
            ModerationConfig(inputs, False, audio_enabled),
        )
        monkeypatch.setattr("paid_gateway.app.mp4_duration", lambda _: Fraction(5))
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(gateway), base_url="https://test"
            ) as client:
                response = await client.post(
                    "/v1/videos/sync",
                    content=body,
                    headers={
                        "Authorization": "Bearer " + KEY,
                        "Content-Type": CONTENT_TYPE,
                        "X-Moderation-Inputs": str(not inputs).lower(),
                        "X-Moderation-Audio": str(not audio_enabled).lower(),
                    },
                )
            required = inputs or audio_enabled
            blocked = required and failure is not None
            expected_status = (400 if failure == "block" else 503) if blocked else 200
            assert response.status_code == expected_status, response.text
            assert len(sent) == (0 if blocked else 1)
            assert len(checked) == int(required)
            assert billing.calls[0][1]["input_modalities"] == ["text", "image", "audio"]
            if blocked:
                assert "private audio" not in response.text
        finally:
            await gateway.close()

    asyncio.run(case())


def test_audio_only_config_requires_worker_and_defaults_off():
    assert ModerationConfig.from_env({}).audio_enabled is False
    config = ModerationConfig.from_env(
        {
            "CCS_MODERATION_PRE_ENABLED": "false",
            "CCS_MODERATION_POST_ENABLED": "false",
            "CCS_MODERATION_AUDIO_ENABLED": "true",
        }
    )
    assert config == ModerationConfig(False, False, True)
    assert config.required


@pytest.mark.parametrize("result", ["allow", "invalid", "unavailable"])
def test_mp3_requires_worker_validation_even_when_all_classifiers_off(result, monkeypatch):
    async def case():
        order = []
        body = voice_form([b"ID3bounded-payload"], mime="audio/mpeg")
        assert parse_video(body, CONTENT_TYPE).needs_audio_validation

        async def worker(request):
            order.append("validate")
            assert request.headers["x-moderation-inputs"] == "false"
            assert request.headers["x-moderation-audio"] == "false"
            if result == "invalid":
                return httpx.Response(
                    400, json={"decision": "block", "category": "invalid_request"}
                )
            if result == "unavailable":
                raise httpx.ConnectError("private")
            return httpx.Response(200, json=ALLOW.wire())

        async def model(request):
            order.append("generate")
            assert request.content == body
            return httpx.Response(200, content=b"video", headers={"content-type": "video/mp4"})

        gateway = PaidGateway(
            downstream,
            FakeBilling("absent"),
            httpx.AsyncClient(transport=httpx.MockTransport(model)),
            ModerationClient(httpx.AsyncClient(transport=httpx.MockTransport(worker))),
            ModerationConfig(False, False, False),
        )
        monkeypatch.setattr("paid_gateway.app.mp4_duration", lambda _: Fraction(5))
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(gateway), base_url="https://test"
            ) as client:
                response = await client.post(
                    "/v1/videos/sync",
                    content=body,
                    headers={
                        "Authorization": "Bearer " + KEY,
                        "Content-Type": CONTENT_TYPE,
                        "X-Moderation-Audio": "false",
                        "X-Moderation-Inputs": "false",
                    },
                )
            assert (
                response.status_code == {"allow": 200, "invalid": 400, "unavailable": 503}[result]
            )
            assert order == (["validate", "generate"] if result == "allow" else ["validate"])
            if result == "invalid":
                assert response.json()["error"]["code"] == "invalid_request"
        finally:
            await gateway.close()

    asyncio.run(case())
