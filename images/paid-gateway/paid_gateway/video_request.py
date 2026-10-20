"""Validate bounded native MiniMax form fields without rewriting multipart bytes."""

from __future__ import annotations

import math
from email import policy
from email.parser import BytesHeaderParser
from typing import Any

from .client import GatewayError, strict_json
from .protocol import VIDEO


def invalid() -> None:
    raise GatewayError("invalid_request", 400)


def number(value: Any, low: float, high: float, *, whole: bool = False) -> int | float:
    try:
        if isinstance(value, bool):
            invalid()
        parsed = int(value) if whole else float(value)
        if not math.isfinite(parsed) or not low <= parsed <= high:
            invalid()
        return parsed
    except (TypeError, ValueError, OverflowError):
        invalid()
    raise AssertionError("unreachable")


def validate_video(body: bytes, content_type: str) -> list[str]:
    try:
        header = BytesHeaderParser(policy=policy.HTTP).parsebytes(
            b"Content-Type: " + content_type.encode("ascii") + b"\r\n\r\n"
        )
        boundary = header.get_boundary()
        if (
            header.get_content_type() != "multipart/form-data"
            or not boundary
            or not 1 <= len(boundary) <= 70
            or not boundary.isascii()
        ):
            invalid()
        marker = b"--" + boundary.encode()
        if not body.startswith(marker + b"\r\n") or body.count(b"\r\n" + marker) > 128:
            invalid()
        sections = body[len(marker) + 2 :].split(b"\r\n" + marker)
        if sections[-1] not in {b"--", b"--\r\n"}:
            invalid()
        fields: dict[str, str] = {}
        files: list[str] = []
        for index, section in enumerate(sections[:-1]):
            if index:
                if not section.startswith(b"\r\n"):
                    invalid()
                section = section[2:]
            headers, separator, data = section.partition(b"\r\n\r\n")
            if not separator or len(headers) > 8192 or headers.count(b"\r\n") > 30:
                invalid()
            part = BytesHeaderParser(policy=policy.HTTP).parsebytes(headers + b"\r\n\r\n")
            if part.defects or any(
                k.lower() not in {"content-disposition", "content-type"} for k in part
            ):
                invalid()
            if (
                len(part.get_all("content-disposition", [])) != 1
                or len(part.get_all("content-type", [])) > 1
            ):
                invalid()
            if part.get_content_disposition() != "form-data":
                invalid()
            name = part.get_param("name", header="content-disposition")
            filename = part.get_filename()
            if not isinstance(name, str) or len(name) > 128:
                invalid()
            if filename is not None:
                if (
                    name not in {"input_reference", "input_references"}
                    or len(files) >= 2
                    or not data
                ):
                    invalid()
                if (
                    len(filename) > 128
                    or any(c in filename for c in ("/", "\\", "\x00", "\r", "\n"))
                    or ".." in filename
                ):
                    invalid()
                media_type = part.get_content_type()
                if media_type == "image/png":
                    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                        invalid()
                elif media_type == "image/jpeg":
                    if not data.startswith(b"\xff\xd8\xff"):
                        invalid()
                else:
                    invalid()
                files.append(name)
                continue
            if name in fields or len(data) > 65536:
                invalid()
            fields[name] = data.decode("utf-8")
        allowed = {
            "model",
            "prompt",
            "width",
            "height",
            "size",
            "seconds",
            "num_frames",
            "fps",
            "aspect_ratio",
            "short_edge",
            "num_inference_steps",
            "flow_shift",
            "guidance_scale",
            "guidance_scale_2",
            "true_cfg_scale",
            "seed",
            "negative_prompt",
            "num_outputs_per_prompt",
            "generate_sound",
            "extra_params",
        }
        if set(fields) - allowed or fields.get("model") != VIDEO or not fields.get("prompt"):
            invalid()
        if files.count("input_reference") > 1 or (
            "input_reference" in files and "input_references" in files
        ):
            invalid()
        if (
            "num_outputs_per_prompt" in fields
            and number(fields["num_outputs_per_prompt"], 1, 1, whole=True) != 1
        ):
            invalid()
        if "fps" in fields:
            number(fields["fps"], 24, 24, whole=True)
        if "num_frames" in fields:
            number(fields["num_frames"], 96, 360, whole=True)
        if "seconds" in fields:
            number(fields["seconds"], 4, 15, whole=True)
        if "num_inference_steps" in fields:
            number(fields["num_inference_steps"], 2, 128, whole=True)
        if "seed" in fields:
            number(fields["seed"], -(2**63), 2**63 - 1, whole=True)
        for key in ("flow_shift", "guidance_scale", "guidance_scale_2", "true_cfg_scale"):
            if key in fields:
                number(fields[key], 0, 32)
        if "short_edge" in fields:
            number(fields["short_edge"], 768, 768, whole=True)
        if "aspect_ratio" in fields:
            ratio = fields["aspect_ratio"]
            if len(ratio) > 32 or ratio.count(":") != 1:
                invalid()
            left, right = ratio.split(":")
            numerator = number(left, 1, 4096, whole=True)
            denominator = number(right, 1, 4096, whole=True)
            if max(numerator, denominator) > 4 * min(numerator, denominator):
                invalid()
        width, height = fields.get("width"), fields.get("height")
        if "size" in fields:
            if width or height or fields["size"].count("x") != 1:
                invalid()
            width, height = fields["size"].split("x")
        if (width is None) != (height is None):
            invalid()
        if width is not None and height is not None:
            w, h = number(width, 32, 3072, whole=True), number(height, 32, 3072, whole=True)
            if w * h > 3072 * 768 or max(w, h) > 4 * min(w, h):
                invalid()
        if "generate_sound" in fields and fields["generate_sound"].lower() not in {
            "true",
            "false",
            "1",
            "0",
        }:
            invalid()
        if "extra_params" in fields:
            extra = strict_json(fields["extra_params"])
            if not isinstance(extra, dict) or set(extra) - {
                "task",
                "duration",
                "duration_seconds",
                "audio_flow_shift",
            }:
                invalid()
            if "task" in extra and extra["task"] not in {"t2va", "fl2va", "i2va"}:
                invalid()
            for key in ("duration", "duration_seconds"):
                if key in extra:
                    number(extra[key], 4, 15)
            if "audio_flow_shift" in extra:
                number(extra["audio_flow_shift"], 0, 32)
    except (UnicodeError, TypeError, ValueError, IndexError) as exc:
        raise GatewayError("invalid_request", 400) from exc
    return ["text", "image"] if files else ["text"]
