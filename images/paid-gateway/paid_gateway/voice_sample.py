"""Bounded voice uploads: full PCM validation and MP3 envelopes requiring worker decode."""

from __future__ import annotations

import io
import struct
import wave
from fractions import Fraction

from .client import GatewayError

MAX_VOICE_BYTES = 3 * 1024 * 1024


def validate_mp3(data: bytes) -> None:
    """Bound the upload; the worker must fully decode MP3 before any generation."""
    if not 4 <= len(data) <= MAX_VOICE_BYTES or not (
        data.startswith(b"ID3") or (data[0] == 0xFF and data[1] & 0xE0 == 0xE0)
    ):
        raise GatewayError("invalid_request", 400)


def voice_duration(data: bytes) -> Fraction:
    """Fully validate one 16-bit mono/stereo PCM sample without decoding compressed media."""
    try:
        if (
            not 44 <= len(data) <= MAX_VOICE_BYTES
            or data[:4] != b"RIFF"
            or data[8:12] != b"WAVE"
            or struct.unpack_from("<I", data, 4)[0] != len(data) - 8
        ):
            raise ValueError
        # Reject truncated, duplicated and ambiguous chunks rather than letting
        # the gateway and model decoders select different audio data.
        offset = 12
        kinds: list[bytes] = []
        while offset < len(data):
            kind, size = struct.unpack_from("<4sI", data, offset)
            offset += 8
            if offset + size > len(data):
                raise ValueError
            if kind == b"fmt ":
                if size != 16:
                    raise ValueError
                encoding, channels, rate, byte_rate, alignment, bits = struct.unpack_from(
                    "<HHIIHH", data, offset
                )
                if (
                    encoding != 1
                    or bits != 16
                    or alignment != channels * 2
                    or byte_rate != rate * alignment
                ):
                    raise ValueError
            if kind not in {b"fmt ", b"data", b"LIST", b"JUNK"}:
                raise ValueError
            kinds.append(kind)
            offset += size + size % 2
        if (
            offset != len(data)
            or kinds.count(b"fmt ") != 1
            or kinds.count(b"data") != 1
            or kinds.index(b"fmt ") > kinds.index(b"data")
        ):
            raise ValueError
        with wave.open(io.BytesIO(data), "rb") as sample:
            channels, width, rate, frames, compression, _ = sample.getparams()
            duration = Fraction(frames, rate)
            if (
                channels not in {1, 2}
                or width != 2
                or not 16000 <= rate <= 48000
                or compression != "NONE"
                or not 2 <= duration <= 15
                or len(sample.readframes(frames + 1)) != frames * channels * width
            ):
                raise ValueError
        return duration
    except (ValueError, EOFError, wave.Error, struct.error, ZeroDivisionError) as exc:
        raise GatewayError("invalid_request", 400) from exc
