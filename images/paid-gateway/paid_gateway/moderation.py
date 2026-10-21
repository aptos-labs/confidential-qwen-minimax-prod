"""Mandatory moderation calls; no environment switch or absent-route bypass."""

from __future__ import annotations

import asyncio

import httpx

from .client import GatewayError
from .moderation_contract import FAILURE, ModerationFailure, Verdict

WORKER_URL = "http://moderation-worker:8090"
PRE_TIMEOUT = 120
POST_TIMEOUT = 240


class ModerationClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(POST_TIMEOUT, connect=5),
            trust_env=False,
            follow_redirects=False,
        )

    async def close(self) -> None:
        await self.client.aclose()

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
