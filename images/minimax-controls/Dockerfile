FROM us-docker.pkg.dev/aptos-registry/ccs-images-public/minimax-watermark@sha256:7b8454effe86fa13c8f80f4481c2fdaca0ce7fe321f8327a37858afe1448fbb7 AS upstream

FROM upstream AS runtime-base
COPY --chmod=0444 install.py manifest.json /opt/minimax-controls/
RUN chmod 0555 /opt/minimax-controls && python3 /opt/minimax-controls/install.py

FROM runtime-base AS test
RUN python3 -m pip install --no-cache-dir pytest==9.1.1
RUN --network=none \
    --mount=type=bind,from=upstream,source=/usr/local/lib/python3.12/dist-packages/vllm_omni,target=/upstream,readonly \
    --mount=type=bind,source=tests,target=/opt/minimax-controls/tests,readonly \
    --mount=type=tmpfs,target=/tmp \
    PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider \
    --confcutdir=/opt/minimax-controls /opt/minimax-controls/tests --upstream=/upstream

FROM runtime-base AS runtime
