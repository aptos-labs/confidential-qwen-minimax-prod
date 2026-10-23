"""Bounded local decoding. Caller owns a request-private tmpfs directory."""

from __future__ import annotations

import asyncio
import io
import math
import os
import shutil
import signal
import sys
import warnings
import wave
from dataclasses import dataclass
from pathlib import Path

from paid_gateway.client import GatewayError
from paid_gateway.media import mp4_metadata
from paid_gateway.moderation_contract import ModerationFailure, strict_object
from paid_gateway.voice_sample import validate_mp3, voice_duration
from PIL import Image

MAX_IMAGE_PIXELS = 16 * 1024 * 1024
MAX_IMAGE_BYTES = 32 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


def checked_image(data: bytes, mime: str = "image/png") -> bytes:
    if len(data) > MAX_IMAGE_BYTES or mime not in {"image/png", "image/jpeg"}:
        raise ModerationFailure
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if (
                    image.format != {"image/png": "PNG", "image/jpeg": "JPEG"}[mime]
                    or getattr(image, "n_frames", 1) != 1
                    or image.width * image.height > MAX_IMAGE_PIXELS
                ):
                    raise ModerationFailure
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                result = io.BytesIO()
                image.convert("RGB").save(result, "PNG")
                if result.tell() > MAX_IMAGE_BYTES:
                    raise ModerationFailure
                return result.getvalue()
    except (
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ) as exc:
        raise ModerationFailure from exc


async def run_decoder(
    program: str, args: list[str], *, capture: bool = False, invalid_input: bool = False
) -> bytes:
    executable = shutil.which(program)
    if executable is None:
        raise ModerationFailure
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "moderation_worker.decoder_limit",
        executable,
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE if capture else asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        async with asyncio.timeout(40):
            data = bytearray()
            if capture:
                assert process.stdout is not None
                while chunk := await process.stdout.read(4096):
                    data.extend(chunk)
                    if len(data) > 32768:
                        raise ModerationFailure
            code = await process.wait()
            if invalid_input and code > 0:
                raise GatewayError("invalid_request", 400)
            if code != 0:
                raise ModerationFailure
            return bytes(data)
    finally:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()


@dataclass(frozen=True)
class Decoded:
    frames: tuple[Path, ...]
    audio: Path | None


async def decode_voice(data: bytes, directory: Path, mime: str = "audio/wav") -> bytes:
    """Fully decode bounded WAV/MP3 and normalize for Qwen, without any network access."""
    duration = None
    if mime == "audio/wav":
        duration = voice_duration(data)
    elif mime == "audio/mpeg":
        validate_mp3(data)
    else:
        raise GatewayError("invalid_request", 400)
    source, target = directory / "voice.wav", directory / "voice-24k.wav"
    source.write_bytes(data)
    source.chmod(0o600)
    await run_decoder(
        "ffmpeg",
        [
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-xerror",
            "-y",
            "-max_alloc",
            str(16 * 1024**2),
            "-protocol_whitelist",
            "file,pipe",
            "-f",
            "wav" if mime == "audio/wav" else "mp3",
            "-err_detect",
            "explode",
            "-threads",
            "1",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "2",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            "-threads",
            "1",
            "-fs",
            "2000000",
            str(target),
        ],
        invalid_input=True,
    )
    with wave.open(str(target), "rb") as wav:
        count = wav.getnframes()
        if (
            wav.getnchannels() != 2
            or wav.getsampwidth() != 2
            or wav.getframerate() != 24000
            or not 2 * 24000 <= count <= 15 * 24000
            or (duration is not None and abs(count - duration * 24000) > 1)
            or len(wav.readframes(count + 1)) != count * 4
        ):
            raise GatewayError("invalid_request", 400)
    return target.read_bytes()


async def decode_video(data: bytes, directory: Path, *, audio_expected: bool) -> Decoded:
    metadata = mp4_metadata(data)
    if not 4 <= metadata.duration <= 15:
        raise ModerationFailure
    source = directory / "input.mp4"
    source.write_bytes(data)
    source.chmod(0o600)
    probe = strict_object(
        await run_decoder(
            "ffprobe",
            [
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-threads",
                "2",
                "-count_frames",
                "-show_entries",
                "stream=codec_type,nb_read_frames",
                "-of",
                "json",
                str(source),
            ],
            capture=True,
        )
    )
    streams = probe.get("streams")
    if not isinstance(streams, list) or not all(isinstance(s, dict) for s in streams):
        raise ModerationFailure
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if (
        len(video) != 1
        or len(audio) > 1
        or len(streams) != len(video) + len(audio)
        or video[0].get("nb_read_frames") != str(metadata.frames)
        or (audio_expected and not audio)
    ):
        raise ModerationFailure
    base = [
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-xerror",
        "-max_alloc",
        str(64 * 1024**2),
        "-protocol_whitelist",
        "file,pipe",
        "-threads",
        "2",
        "-i",
        str(source),
    ]
    # Complete decode first: sampling must not hide corrupt unsampled frames/audio.
    await run_decoder("ffmpeg", [*base, "-map", "0:v:0", "-map", "0:a?", "-f", "null", "-"])
    # The structural meter proves a constant 24 fps presentation timeline; frame
    # indices 0,24,... correspond to t=0,1,... including a partial final second.
    await run_decoder(
        "ffmpeg",
        [
            *base,
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            r"select=not(mod(n\,24))",
            "-fps_mode",
            "vfr",
            "-frames:v",
            "16",
            "-c:v",
            "png",
            "-threads",
            "1",
            str(directory / "frame-%02d.png"),
        ],
    )
    frames = tuple(sorted(directory.glob("frame-*.png")))
    if len(frames) != math.ceil(metadata.duration) or any(
        not 0 < frame.stat().st_size <= MAX_IMAGE_BYTES for frame in frames
    ):
        raise ModerationFailure
    audio_path = None
    if audio:
        audio_path = directory / "audio.wav"
        await run_decoder(
            "ffmpeg",
            [
                *base,
                "-map",
                "0:a:0",
                "-vn",
                "-ac",
                "2",
                "-ar",
                "24000",
                "-c:a",
                "pcm_s16le",
                "-threads",
                "1",
                "-fs",
                "2000000",
                str(audio_path),
            ],
        )
        with wave.open(str(audio_path), "rb") as wav:
            count = wav.getnframes()
            if (
                wav.getnchannels() != 2
                or wav.getsampwidth() != 2
                or wav.getframerate() != 24000
                or not 0 < count <= 16 * 24000
                or len(wav.readframes(count)) != count * 4
            ):
                raise ModerationFailure
    return Decoded(frames, audio_path)
