"""One bounded request at a time; every unexpected failure produces a fixed block."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import tempfile
import wave
from collections.abc import AsyncIterator
from pathlib import Path

from paid_gateway.moderation_contract import ALLOW, UNAVAILABLE, ModerationFailure, Verdict
from paid_gateway.protocol import BODY_LIMIT, OUTPUT_LIMIT
from paid_gateway.video_request import parse_video
from PIL import Image
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .media import checked_image, decode_video
from .models import Guard, Omni, audio_part, image_part, text_part


class Worker:
    def __init__(self, guard: Guard | None = None, omni: Omni | None = None) -> None:
        self.guard = guard or Guard()
        self.omni = omni or Omni()
        self.ready = False
        self.busy = False

    async def pre(self, body: bytes, content_type: str) -> Verdict:
        video = parse_video(body, content_type)
        verdict = await self.guard.check(video.prompt)
        if verdict.decision == "block":
            return verdict
        # Always run the exact-policy pass: a Safe Guard label is not full AUP coverage.
        verdict = await self.omni.classify(
            [
                text_part(
                    json.dumps(
                        {
                            "prompt": video.prompt,
                            "negative_prompt": video.negative_prompt,
                        },
                        ensure_ascii=False,
                    )
                )
            ]
        )
        if verdict.decision == "block":
            return verdict
        for mime, raw in video.images:
            image = checked_image(raw, mime)
            verdict = await self.omni.classify(
                [
                    text_part("Classify this uploaded conditioning image."),
                    image_part(image),
                ]
            )
            if verdict.decision == "block":
                return verdict
        return ALLOW

    async def post(self, body: bytes, audio_expected: bool) -> Verdict:
        with tempfile.TemporaryDirectory(prefix="moderation-") as scratch:
            decoded = await decode_video(body, Path(scratch), audio_expected=audio_expected)
            for frame in decoded.frames:
                verdict = await self.omni.classify(
                    [
                        text_part("Classify this generated video frame."),
                        image_part(frame.read_bytes()),
                    ]
                )
                if verdict.decision == "block":
                    return verdict
            if decoded.audio is not None:
                verdict = await self.omni.classify(
                    [
                        text_part(
                            "Classify the entire generated audio track, including lyrics and sounds."
                        ),
                        audio_part(decoded.audio.read_bytes()),
                    ]
                )
                if verdict.decision == "block":
                    return verdict
            return ALLOW

    async def check(self, request: Request) -> JSONResponse:
        if not self.ready or not self.guard.healthy or self.busy:
            return JSONResponse(UNAVAILABLE.wire(), status_code=503)
        self.busy = True  # no await between admission and reservation
        try:
            limit = BODY_LIMIT if request.url.path == "/pre" else OUTPUT_LIMIT
            async with asyncio.timeout(110 if request.url.path == "/pre" else 230):
                raw = bytearray()
                async for part in request.stream():
                    if len(raw) + len(part) > limit:
                        raise ModerationFailure
                    raw.extend(part)
                content_types = request.headers.getlist("content-type")
                if len(content_types) != 1:
                    raise ModerationFailure
                if request.url.path == "/pre":
                    operation = asyncio.create_task(self.pre(bytes(raw), content_types[0]))
                else:
                    expected = request.headers.getlist("x-audio-expected")
                    if content_types != ["video/mp4"] or expected not in [["true"], ["false"]]:
                        raise ModerationFailure
                    operation = asyncio.create_task(self.post(bytes(raw), expected == ["true"]))

                async def disconnected() -> None:
                    while True:
                        if (await request.receive())["type"] == "http.disconnect":
                            return

                watcher = asyncio.create_task(disconnected())
                try:
                    done, _ = await asyncio.wait(
                        {operation, watcher}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if watcher in done:
                        raise ModerationFailure
                    verdict = await operation
                finally:
                    watcher.cancel()
                    if not operation.done():
                        operation.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await watcher
                    with contextlib.suppress(asyncio.CancelledError):
                        await operation
                return JSONResponse(verdict.wire())
        except Exception:
            # No exception text, model response, prompt, media, or traceback escapes.
            return JSONResponse(UNAVAILABLE.wire(), status_code=503)
        finally:
            self.busy = False

    async def initialize(self) -> None:
        try:
            await asyncio.to_thread(self.guard.load)
            if await self.guard.check("Draw a red square.") != ALLOW:
                return
            png = io.BytesIO()
            Image.new("RGB", (32, 32), "red").save(png, "PNG")
            wav = io.BytesIO()
            with wave.open(wav, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(24000)
                output.writeframes(b"\0\0" * 24000)
            # Qwen may boot after this container. Retry readiness, never requests.
            while self.guard.healthy:
                try:
                    for content in (
                        [text_part("Draw a red square.")],
                        [text_part("Classify this image."), image_part(png.getvalue())],
                        [text_part("Classify this audio."), audio_part(wav.getvalue())],
                    ):
                        if await self.omni.classify(content) != ALLOW:
                            raise ModerationFailure
                    self.ready = True
                    return
                except Exception:
                    await asyncio.sleep(15)
        except Exception:
            self.ready = False


def create_app(worker: Worker | None = None) -> Starlette:
    worker = worker or Worker()

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        task = asyncio.create_task(worker.initialize())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            await worker.omni.close()

    async def alive(request: Request) -> JSONResponse:
        return JSONResponse({"status": "alive"})

    async def ready(request: Request) -> JSONResponse:
        ok = worker.ready and worker.guard.healthy
        return JSONResponse(
            {"status": "ready" if ok else "unavailable"}, status_code=200 if ok else 503
        )

    return Starlette(
        routes=[
            Route("/pre", worker.check, methods=["POST"]),
            Route("/post", worker.check, methods=["POST"]),
            Route("/health/liveliness", alive),
            Route("/health/readiness", ready),
        ],
        lifespan=lifespan,
    )
