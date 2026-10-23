"""Exercise actual ASGI send events: zero video headers/bytes before all checks allow."""

import asyncio
from fractions import Fraction

import httpx
import pytest
from paid_gateway.app import PaidGateway
from paid_gateway.moderation import ModerationClient, ModerationConfig
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


def test_aggregate_health_reports_unavailable_guard():
    async def case():
        async def model(request):
            return httpx.Response(200, json={"status": "ok"})

        async def worker(request):
            return httpx.Response(503)

        gateway = PaidGateway(
            downstream,
            FakeBilling("absent"),
            httpx.AsyncClient(transport=httpx.MockTransport(model)),
            ModerationClient(httpx.AsyncClient(transport=httpx.MockTransport(worker))),
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(gateway), base_url="https://test"
            ) as client:
                result = await client.get("/health")
            assert result.status_code == 503
            assert [b["status"] for b in result.json()["backends"]] == [200, 200, 503]
        finally:
            await gateway.close()

    asyncio.run(case())


@pytest.mark.parametrize(
    "fault",
    ["valid", "ready", "unknown", "extra", "contradiction", "failed_ready", "large", "timeout"],
)
def test_startup_health_only_exports_allowlisted_diagnostics(fault):
    async def case():
        async def worker(request):
            assert request.url.path == "/health/startup"
            value = {"status": "unavailable", "stage": "weights_load", "failed": True}
            status = 503
            if fault in {"ready", "failed_ready"}:
                value = {"status": "ready", "stage": "ready", "failed": fault == "failed_ready"}
                status = 200
            elif fault == "unknown":
                value["stage"] = "private prompt or exception text"
            elif fault == "extra":
                value["exception"] = "private"
            elif fault == "contradiction":
                status = 200
            elif fault == "large":
                return httpx.Response(
                    503, content=b"x" * 1025, headers={"content-type": "application/json"}
                )
            elif fault == "timeout":
                raise httpx.ReadTimeout("private")
            return httpx.Response(status, json=value)

        async with httpx.AsyncClient(transport=httpx.MockTransport(worker)) as client:
            result = await ModerationClient(client).startup_health()
        expected = (
            (False, "weights_load", True) if fault == "valid" else (False, "unreachable", False)
        )
        if fault == "ready":
            expected = (True, "ready", False)
        assert result == expected
        assert "private" not in str(result)

    asyncio.run(case())


def test_moderation_config_defaults_and_independent_controls():
    assert ModerationConfig.from_env({}) == ModerationConfig(True, True)
    assert ModerationConfig.from_env({"CCS_MODERATION_POST_ENABLED": "false"}) == (
        ModerationConfig(True, False)
    )
    assert ModerationConfig.from_env({"CCS_MODERATION_PRE_ENABLED": "false"}) == (
        ModerationConfig(False, True)
    )


@pytest.mark.parametrize("name", ["CCS_MODERATION_PRE_ENABLED", "CCS_MODERATION_POST_ENABLED"])
@pytest.mark.parametrize("value", ["", "0", "1", "False", "off", " false", "private-invalid"])
def test_invalid_moderation_config_aborts_before_clients(name, value, monkeypatch):
    monkeypatch.setenv(name, value)

    def forbidden():
        raise AssertionError("must reject configuration before creating clients")

    monkeypatch.setattr("paid_gateway.app.production_billing", forbidden)
    with pytest.raises(ValueError, match=name) as error:
        PaidGateway(downstream)
    assert "private-invalid" not in str(error.value)


@pytest.mark.parametrize(
    "pre_enabled,post_enabled", [(True, True), (True, False), (False, True), (False, False)]
)
@pytest.mark.parametrize("billing_mode", [None, "absent"])
def test_configured_stages_preserve_admission_and_video_delivery(
    pre_enabled, post_enabled, billing_mode, monkeypatch
):
    async def case():
        order = []

        async def classifier(request):
            order.append(request.url.path)
            assert "authorization" not in request.headers
            return httpx.Response(200, json=ALLOW.wire())

        async def model(request):
            order.append("generate")
            return httpx.Response(
                200, content=b"original video", headers={"content-type": "video/mp4"}
            )

        billing = FakeBilling(billing_mode)
        monkeypatch.setenv("CCS_MODERATION_PRE_ENABLED", str(pre_enabled).lower())
        monkeypatch.setenv("CCS_MODERATION_POST_ENABLED", str(post_enabled).lower())
        monkeypatch.setattr(
            "test_gateway.maximum",
            lambda _: {
                "model": "minimax-h3-fl2va",
                "meter": {
                    "kind": "generated_video_seconds",
                    "usage": {"numerator": 15, "denominator": 1},
                },
            },
        )
        gateway = PaidGateway(
            downstream,
            billing,
            httpx.AsyncClient(transport=httpx.MockTransport(model)),
            ModerationClient(httpx.AsyncClient(transport=httpx.MockTransport(classifier))),
        )
        # Settings are captured at startup, never re-read from requests or environment.
        monkeypatch.setenv("CCS_MODERATION_PRE_ENABLED", str(not pre_enabled).lower())
        monkeypatch.setenv("CCS_MODERATION_POST_ENABLED", str(not post_enabled).lower())
        monkeypatch.setattr("paid_gateway.app.mp4_duration", lambda _: Fraction(4))
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(gateway), base_url="https://test"
            ) as client:
                body = form([("model", "minimax-h3-fl2va"), ("prompt", "fixture")])
                headers = {"Content-Type": "multipart/form-data; boundary=fixture-boundary"}
                denied = await client.post("/v1/videos/sync", content=body, headers=headers)
                assert denied.status_code == 401 and not order and not billing.calls
                headers["Authorization"] = "Bearer " + KEY
                result = await client.post("/v1/videos/sync", content=body, headers=headers)
            assert result.status_code == 200 and result.content == b"original video"
            assert order == (["/pre"] if pre_enabled else []) + ["generate"] + (
                ["/post"] if post_enabled else []
            )
            assert [op for op, _ in billing.calls] == (
                ["admit"] if billing_mode == "absent" else ["admit", "checkpoint", "settle"]
            )
            if billing_mode is None:
                assert billing.calls[-1][1]["reason"] == "completed"
        finally:
            await gateway.close()

    asyncio.run(case())


@pytest.mark.parametrize("fault", ["block", "timeout", "invalid"])
def test_precheck_still_blocks_with_postcheck_disabled(fault):
    async def case():
        calls = []

        async def classifier(request):
            calls.append(request.url.path)
            if fault == "timeout":
                raise httpx.ReadTimeout("private")
            if fault == "invalid":
                return httpx.Response(200, text="private")
            return httpx.Response(200, json={"decision": "block", "category": "sexual_content"})

        async def model(request):
            raise AssertionError("blocked input reached generation")

        gateway = PaidGateway(
            downstream,
            FakeBilling("absent"),
            httpx.AsyncClient(transport=httpx.MockTransport(model)),
            ModerationClient(httpx.AsyncClient(transport=httpx.MockTransport(classifier))),
            ModerationConfig(True, False),
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(gateway), base_url="https://test"
            ) as client:
                result = await client.post(
                    "/v1/videos/sync",
                    content=form([("model", "minimax-h3-fl2va"), ("prompt", "fixture")]),
                    headers={
                        "Authorization": "Bearer " + KEY,
                        "Content-Type": "multipart/form-data; boundary=fixture-boundary",
                        # Request headers cannot override the operator's startup config.
                        "CCS_MODERATION_PRE_ENABLED": "false",
                    },
                )
            assert result.status_code == (400 if fault == "block" else 503)
            assert calls == ["/pre"] and "video/mp4" not in result.headers["content-type"]
        finally:
            await gateway.close()

    asyncio.run(case())


@pytest.mark.parametrize(
    "pre_enabled,post_enabled", [(True, True), (True, False), (False, True), (False, False)]
)
def test_health_only_requires_worker_for_enabled_stages(pre_enabled, post_enabled):
    async def case():
        calls = []

        async def worker(request):
            calls.append(request.url.path)
            raise httpx.ConnectError("unavailable")

        gateway = PaidGateway(
            downstream,
            FakeBilling("absent"),
            httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))),
            ModerationClient(httpx.AsyncClient(transport=httpx.MockTransport(worker))),
            ModerationConfig(pre_enabled, post_enabled),
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(gateway), base_url="https://test"
            ) as client:
                response = await client.get("/health")
            required = pre_enabled or post_enabled
            assert response.status_code == (503 if required else 200)
            assert calls == (["/health/startup"] if required else [])
            assert len(response.json()["backends"]) == (3 if required else 2)
            assert response.json()["moderation"] == {
                "pre_enabled": pre_enabled,
                "post_enabled": post_enabled,
            }
        finally:
            await gateway.close()

    asyncio.run(case())
