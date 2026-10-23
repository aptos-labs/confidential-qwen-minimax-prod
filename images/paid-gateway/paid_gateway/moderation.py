"""Operator-configured moderation stages with fail-closed calls when enabled."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

from .client import GatewayError
from .moderation_contract import FAILURE, STARTUP_STAGES, ModerationFailure, Verdict, strict_object

WORKER_URL = "http://moderation-worker:8090"
PRE_TIMEOUT = 120
POST_TIMEOUT = 240


@dataclass(frozen=True)
class ModerationConfig:
    pre_enabled: bool = True
    post_enabled: bool = True

    @property
    def required(self) -> bool:
        return self.pre_enabled or self.post_enabled

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ModerationConfig:
        values = os.environ if env is None else env

        def enabled(name: str) -> bool:
            value = values.get(name, "true")
            if value not in {"true", "false"}:
                raise ValueError(f"{name} must be true or false")
            return value == "true"

        return cls(
            pre_enabled=enabled("CCS_MODERATION_PRE_ENABLED"),
            post_enabled=enabled("CCS_MODERATION_POST_ENABLED"),
        )


class ModerationClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(POST_TIMEOUT, connect=5),
            trust_env=False,
            follow_redirects=False,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def ready(self) -> bool:
        try:
            async with asyncio.timeout(3):
                async with self.client.stream("GET", WORKER_URL + "/health/readiness") as response:
                    if response.status_code != 200:
                        return False
                    data = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=1024):
                        data.extend(chunk)
                        if len(data) > 1024:
                            return False
                    return strict_object(bytes(data)) == {"status": "ready"}
        except (ModerationFailure, httpx.HTTPError, TimeoutError):
            return False

    async def startup_health(self) -> tuple[bool, str, bool]:
        unavailable = (False, "unreachable", False)
        try:
            async with asyncio.timeout(3):
                async with self.client.stream(
                    "GET",
                    WORKER_URL + "/health/startup",
                    headers={"Accept-Encoding": "identity"},
                ) as response:
                    if (
                        response.status_code not in {200, 503}
                        or response.headers.get("content-type", "").split(";")[0]
                        != "application/json"
                        or response.headers.get("content-encoding", "identity") != "identity"
                    ):
                        return unavailable
                    data = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=1024):
                        data.extend(chunk)
                        if len(data) > 1024:
                            return unavailable
                    value = strict_object(bytes(data))
                    if set(value) != {"status", "stage", "failed"}:
                        return unavailable
                    status, stage, failed = value["status"], value["stage"], value["failed"]
                    if (
                        status not in ("ready", "unavailable")
                        or not isinstance(stage, str)
                        or stage not in STARTUP_STAGES
                        or not isinstance(failed, bool)
                    ):
                        return unavailable
                    ready = status == "ready"
                    if ready != (response.status_code == 200) or ready != (stage == "ready"):
                        return unavailable
                    if ready and failed:
                        return unavailable
                    return ready, stage, failed
        except (ModerationFailure, httpx.HTTPError, TimeoutError):
            return unavailable

    async def check(
        self, stage: str, body: bytes, content_type: str, *, audio_expected: bool = True
    ) -> None:
        if stage not in {"pre", "post"}:
            raise ValueError("invalid moderation stage")
        try:
            async with asyncio.timeout(PRE_TIMEOUT if stage == "pre" else POST_TIMEOUT):
                async with self.client.stream(
                    "POST",
                    f"{WORKER_URL}/{stage}",
                    content=body,
                    headers={
                        "Content-Type": content_type,
                        "Accept-Encoding": "identity",
                        "X-Audio-Expected": "true" if audio_expected else "false",
                    },
                ) as response:
                    if response.status_code != 200 or (
                        response.headers.get("content-type", "").split(";")[0]
                        != "application/json"
                        or response.headers.get("content-encoding", "identity") != "identity"
                    ):
                        raise ModerationFailure
                    data = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=1024):
                        data.extend(chunk)
                        if len(data) > 1024:
                            raise ModerationFailure
                    verdict = Verdict.parse(bytes(data))
        except (ModerationFailure, httpx.HTTPError, TimeoutError) as exc:
            raise GatewayError(FAILURE) from exc
        if verdict.category == FAILURE:
            raise GatewayError(FAILURE)
        if verdict.decision == "block":
            raise GatewayError("content_policy_violation", 400, category=verdict.category)
