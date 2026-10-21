# Build with repository root context; Dockerfile.dockerignore is an allowlist.
FROM docker.io/vllm/vllm-omni@sha256:4780186f168af96634917208596675439fa71cb5ed3440a30e8c21ca1defc451 AS runtime-base
USER root
WORKDIR /opt/moderation
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m pip install --no-cache-dir Pillow==12.2.0 httpx==0.28.1 \
       starlette==0.47.3 uvicorn==0.35.0
COPY images/moderation-worker/download_guard.py /opt/download_guard.py
RUN python3 /opt/download_guard.py \
    && rm /opt/download_guard.py \
    && chmod -R a-w /opt/qwen3guard
COPY --chmod=0444 images/paid-gateway/paid_gateway/*.py /opt/moderation/paid_gateway/
COPY --chmod=0444 images/moderation-worker/moderation_worker/*.py /opt/moderation/moderation_worker/
RUN chmod 0555 /opt/moderation /opt/moderation/paid_gateway /opt/moderation/moderation_worker
ENV PYTHONPATH=/opt/moderation PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
    CUDA_VISIBLE_DEVICES=7 TOKENIZERS_PARALLELISM=false
ENTRYPOINT ["python3", "-m", "moderation_worker"]
CMD []

FROM runtime-base AS test
RUN python3 -m pip install --no-cache-dir pytest==9.1.1
RUN --network=none \
    --mount=type=bind,source=images/moderation-worker/tests,target=/opt/moderation/tests,readonly \
    --mount=type=tmpfs,target=/tmp \
    python3 -m pytest -q -p no:cacheprovider --confcutdir=/opt/moderation/tests /opt/moderation/tests

FROM runtime-base AS runtime
