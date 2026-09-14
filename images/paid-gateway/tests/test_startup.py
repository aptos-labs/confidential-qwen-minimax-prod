"""Offline startup/security regressions with the real Starlette lifespan."""

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paid_gateway import install

SECRET = "synthetic-reporter-secret-0123456789"


@pytest.mark.parametrize("flag", [None, "0", "1", "malicious"])
def test_unconditional_boundary_and_cleanup(monkeypatch, flag):
    monkeypatch.setenv("USAGE_REPORTER_SECRET", SECRET)
    if flag is None:
        monkeypatch.delenv("CCS_BILLING_REQUIRED", raising=False)
    else:
        monkeypatch.setenv("CCS_BILLING_REQUIRED", flag)
    monkeypatch.setenv("CCS_BILLING_URL", "http://untrusted.invalid")
    monkeypatch.setenv("CCS_BILLING_REPORTER_ID", "untrusted")

    @asynccontextmanager
    async def lifespan(app):
        @app.post("/v1/chat/completions")
        @app.post("/v1/videos/sync")
        async def free():
            raise AssertionError("native inference bypass")

        yield

    app = FastAPI(lifespan=lifespan)
    gateway = install(app)
    assert gateway.billing.base_url == "https://api.inference.aptoslabs.com"
    assert gateway.billing.reporter == "model-router"
    assert not gateway.billing.client.follow_redirects
    with TestClient(app) as client:
        assert client.post("/v1/chat/completions", json={}).status_code == 401
        assert client.post("/v1/videos/sync", json={}).status_code == 401
        assert client.post("/v1/completions", json={}).status_code == 404
        with pytest.raises(RuntimeError, match="already installed"):
            install(app)
    assert gateway.billing.client.is_closed and gateway.upstream.is_closed
    asyncio.run(gateway.close())


@pytest.mark.parametrize("model", ["qwen3-omni", "minimax-h3-fl2va"])
def test_both_production_models_require_admission_before_upstream(monkeypatch, model):
    from paid_gateway.client import BillingClient

    admissions = []

    def deny(request):
        if request.url.path == "/api/internal/billing/admit":
            admissions.append(request)
        else:
            assert request.url.path == "/api/internal/billing/settle"
        return httpx.Response(403, json={"error": "unmapped test key"})

    def upstream_forbidden(request):
        raise AssertionError("unadmitted upstream request")

    def billing():
        return BillingClient(
            "https://api.inference.aptoslabs.com",
            "model-router",
            SECRET.encode(),
            client=httpx.AsyncClient(transport=httpx.MockTransport(deny)),
        )

    monkeypatch.setattr("paid_gateway.app.production_billing", billing)
    app = FastAPI()
    gateway = install(app)
    assert gateway.allowed_models == {"qwen3-omni", "minimax-h3-fl2va"}
    asyncio.run(gateway.upstream.aclose())
    gateway.upstream = httpx.AsyncClient(transport=httpx.MockTransport(upstream_forbidden))
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer synthetic-unmapped-key"}
        if model == "qwen3-omni":
            response = client.post(
                "/v1/chat/completions",
                headers=headers,
                json={"model": model, "messages": [{"role": "user", "content": "hello"}]},
            )
        else:
            response = client.post(
                "/v1/videos/sync",
                headers=headers,
                files=[
                    ("model", (None, model)),
                    ("prompt", (None, "a cube")),
                    ("seconds", (None, "4")),
                ],
            )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "billing_rejected"
        assert len(admissions) == 1
    assert gateway.billing.client.is_closed and gateway.upstream.is_closed


@pytest.mark.parametrize("secret", [None, "", "x" * 31, "x" * 129, "\u00e9" * 32, SECRET + "\n"])
def test_invalid_secret_aborts_before_startup(monkeypatch, secret):
    monkeypatch.delenv("USAGE_REPORTER_SECRET", raising=False)
    if secret is not None:
        monkeypatch.setenv("USAGE_REPORTER_SECRET", secret)
    with pytest.raises(RuntimeError, match="missing or invalid") as error:
        install(FastAPI())
    if secret:
        assert secret not in str(error.value)


def test_late_worker_hook_is_rejected():
    @asynccontextmanager
    async def lifespan(app):
        assert app.middleware_stack is not None
        install(app)
        yield

    with (
        pytest.raises(RuntimeError, match="before server startup"),
        TestClient(FastAPI(lifespan=lifespan)),
    ):
        pass


@pytest.mark.parametrize("stage", ["startup", "shutdown"])
def test_failed_lifespan_closes_clients(monkeypatch, stage):
    monkeypatch.setenv("USAGE_REPORTER_SECRET", SECRET)

    @asynccontextmanager
    async def lifespan(app):
        if stage == "startup":
            raise RuntimeError("synthetic failure")
        yield
        raise RuntimeError("synthetic failure")

    app = FastAPI(lifespan=lifespan)
    gateway = install(app)
    with pytest.raises(RuntimeError, match="synthetic failure"), TestClient(app):
        pass
    assert gateway.billing.client.is_closed and gateway.upstream.is_closed
