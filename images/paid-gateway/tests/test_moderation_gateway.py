"""Exercise actual ASGI send events: zero video headers/bytes before all checks allow."""

import asyncio
from fractions import Fraction

import httpx
import pytest
from paid_gateway.app import PaidGateway
from paid_gateway.moderation import ModerationClient
from paid_gateway.moderation_contract import ALLOW
from test_gateway import KEY, FakeBilling, downstream, form


@pytest.mark.parametrize("stage", ["pre", "post"])
@pytest.mark.parametrize("fault", ["block", "timeout", "missing", "invalid", "large", "redirect"])
@pytest.mark.parametrize("billing_mode", [None, "absent"])
def test_moderation_failure_never_emits_video(stage, fault, billing_mode, monkeypatch):
    async def case():
        calls, sent, events = [], [], []

        async def classifier(request):
            calls.append(request.url.path)
            assert "authorization" not in request.headers
            if request.url.path != "/" + stage:
                return httpx.Response(200, json=ALLOW.wire())
            if fault == "block":
                return httpx.Response(
                    200, json={"decision": "block", "category": "sexual_content"}
                )
            if fault == "timeout":
                raise httpx.ReadTimeout("private")
            if fault == "missing":
                return httpx.Response(404)
            if fault == "redirect":
                return httpx.Response(302, headers={"location": "https://outside.invalid"})
            return httpx.Response(
                200,
                content=b"x" * (1025 if fault == "large" else 1),
                headers={"content-type": "application/json"},
            )

        async def model(request):
            sent.append(request)
            return httpx.Response(
                200, content=b"private video", headers={"content-type": "video/mp4"}
            )

        billing = FakeBilling(billing_mode)
        moderation = ModerationClient(httpx.AsyncClient(transport=httpx.MockTransport(classifier)))
        gateway = PaidGateway(
            downstream,
            billing,
            httpx.AsyncClient(transport=httpx.MockTransport(model)),
            moderation,
        )
        monkeypatch.setattr("paid_gateway.app.mp4_duration", lambda _: Fraction(4))

        async def observed(scope, receive, send):
            async def capture(event):
                events.append(event)
                await send(event)

            await gateway(scope, receive, capture)

        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(observed), base_url="https://test"
            ) as client:
                result = await client.post(
                    "/v1/videos/sync",
                    content=form([("model", "minimax-h3-fl2va"), ("prompt", "private prompt")]),
                    headers={
                        "Authorization": "Bearer " + KEY,
                        "Content-Type": "multipart/form-data; boundary=fixture-boundary",
                    },
                )
            assert result.status_code == (400 if fault == "block" else 503)
            assert result.json()["error"]["request_id"] == result.headers["x-request-id"]
            assert "private" not in result.text and KEY not in result.text
            assert len(sent) == (0 if stage == "pre" else 1)
            assert calls == (["/pre"] if stage == "pre" else ["/pre", "/post"])
            assert [op for op, _ in billing.calls] == (
                ["admit"] if billing_mode == "absent" else ["admit", "settle"]
            )
            if billing_mode is None:
                assert billing.calls[-1][1]["reason"] == "cancelled"
            assert not any(
                b"video/mp4" in v for event in events for _, v in event.get("headers", [])
            )
            assert not any(b"private video" in event.get("body", b"") for event in events)
        finally:
            await gateway.close()

    asyncio.run(case())


def test_only_checked_bytes_released_after_postcheck(monkeypatch):
    async def case():
        order = []

        async def classifier(request):
            order.append(request.url.path)
            if request.url.path == "/post":
                assert request.content == b"checked video"
                assert request.headers["x-audio-expected"] == "false"
            return httpx.Response(200, json=ALLOW.wire())

        async def model(request):
            order.append("generate")
            return httpx.Response(
                200, content=b"checked video", headers={"content-type": "video/mp4"}
            )

        gateway = PaidGateway(
            downstream,
            FakeBilling("absent"),
            httpx.AsyncClient(transport=httpx.MockTransport(model)),
            ModerationClient(httpx.AsyncClient(transport=httpx.MockTransport(classifier))),
        )
        monkeypatch.setattr("paid_gateway.app.mp4_duration", lambda _: Fraction(4))
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(gateway), base_url="https://test"
            ) as client:
                result = await client.post(
                    "/v1/videos/sync",
                    content=form(
                        [
                            ("model", "minimax-h3-fl2va"),
                            ("prompt", "fixture"),
                            ("generate_sound", "false"),
                        ]
                    ),
                    headers={
                        "Authorization": "Bearer " + KEY,
                        "Content-Type": "multipart/form-data; boundary=fixture-boundary",
                    },
                )
            assert result.status_code == 200 and result.content == b"checked video"
            assert order == ["/pre", "generate", "/post"]
        finally:
            await gateway.close()

    asyncio.run(case())
