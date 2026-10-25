# syntax=docker/dockerfile:1.7
FROM docker.io/vllm/vllm-openai@sha256:5f5e535216848d0c52159c8c13a0af04be5f6fe1a84e79914300610796f76d40 AS upstream
ADD --checksum=sha256:c56ba8f28cd40dbf621e1260dc9b626c360a64af156aa133279f5cf45da9c054 https://codeload.github.com/vllm-project/vllm-omni/tar.gz/a8576ccb725c4e21cd13c3eb5f9a546b21149d2b /tmp/vllm-omni.tar.gz
ENV VLLM_OMNI_TARGET_DEVICE=cuda SETUPTOOLS_SCM_PRETEND_VERSION=0.30.0
RUN mkdir -p /opt/upstream && tar -xzf /tmp/vllm-omni.tar.gz --strip-components=1 -C /opt/upstream \
    && rm /tmp/vllm-omni.tar.gz \
    && uv pip install --python "$(python3 -c 'import sys; print(sys.executable)')" --no-cache '/opt/upstream[vsa]' \
    && python3 -c "from importlib.metadata import version; assert version('vllm') == version('vllm-omni') == '0.30.0'; assert version('fastvideo-kernel') == '0.3.4'"

FROM upstream AS runtime-base
# Public, immutable adapter; base H3 stays on the existing measured model volume.
ADD --checksum=sha256:42dc502a2078f166c396a1fa75f29728d1844363652d345d5ef3e2b444ed6470 https://huggingface.co/FastVideo/FastVideo-FastH3-4-step-Preview-v1-LoRA/resolve/f509e629374cac104e7f62daecce6d1488a3041d/vsa-datafree/adapter_model.safetensors /opt/fasth3/adapter_model.safetensors
ADD --checksum=sha256:59b99642b95ea21630e311198ddbfffbfe05aadba0c2f5d884cbdf4efcc90f44 https://huggingface.co/FastVideo/FastVideo-FastH3-4-step-Preview-v1-LoRA/resolve/f509e629374cac104e7f62daecce6d1488a3041d/LICENSE /opt/fasth3/LICENSE
COPY --chmod=0444 install.py install_diagnostics.py install_kernel.py serve.py ccs_fasth3_controls.py ccs_fasth3_diagnostics.py /opt/fasth3/
RUN python3 /opt/fasth3/install.py && chmod 0444 /opt/fasth3/adapter_model.safetensors
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
    VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1 PYTHONDONTWRITEBYTECODE=1 \
    TRITON_CACHE_DIR=/var/cache/fasth3/triton TORCHINDUCTOR_CACHE_DIR=/var/cache/fasth3/inductor
ENTRYPOINT []

FROM runtime-base AS test
RUN python3 -m pip install --no-cache-dir pytest==9.1.1
COPY tests /opt/fasth3/tests
RUN --network=none python3 -m pytest -q --confcutdir=/opt/fasth3/tests /opt/fasth3/tests --upstream=/opt/upstream/vllm_omni
# Actual release tests verify fusion completeness, the four-forward schedule and
# VSA task/backend restrictions on CPU, using synthetic weights.
RUN mkdir -p /tmp/fasth3-native-tests \
    && cp /opt/upstream/tests/diffusion/models/minimax_h3/test_minimax_h3_fasth3.py /tmp/fasth3-native-tests/
RUN --network=none python3 -m pytest -q --confcutdir=/tmp/fasth3-native-tests /tmp/fasth3-native-tests

FROM runtime-base AS runtime
