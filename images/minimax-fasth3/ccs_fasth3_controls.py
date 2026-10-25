"""Keep the trial's encoded output within the existing 15-second billing limit."""

from __future__ import annotations

from typing import Any


def bound_media(
    videos: list[Any], audios: list[Any], *, fps: float, sample_rate: int
) -> tuple[list[Any], list[Any]]:
    # Native H3 rounds a 15-second request to 362 frames. Deliver the first
    # 360 at 24 FPS to stay within the gateway's encoded-duration limit.
    if fps != 24 or sample_rate <= 0 or len(videos) != len(audios):
        raise ValueError("unexpected FastH3 output geometry")
    bounded_videos, bounded_audios = [], []
    for video, audio in zip(videos, audios, strict=True):
        if len(getattr(video, "shape", ())) != 4:
            raise ValueError("FastH3 trial requires decoded frames")
        bounded_videos.append(video[:360])
        if audio is not None:
            shape: tuple[int, ...] = getattr(audio, "shape", ())
            if len(shape) == 1:
                audio = audio[: 15 * sample_rate]
            elif len(shape) == 2:
                audio = (
                    audio[:, : 15 * sample_rate] if shape[0] <= 8 else audio[: 15 * sample_rate, :]
                )
            else:
                raise ValueError("unexpected FastH3 audio layout")
        bounded_audios.append(audio)
    return bounded_videos, bounded_audios
