import hashlib
import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "render", ROOT / "scripts/render_moderated_runtime.py"
)
render = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render)

GATEWAY = render.REGISTRY + "confidential-qwen-minimax-paid-gateway@sha256:" + "a" * 64
WORKER = render.REGISTRY + "confidential-qwen-minimax-moderation-worker@sha256:" + "b" * 64


def test_license_snapshot_matches_recorded_hash():
    expected = (ROOT / "docs/minimax-h3-license.sha256").read_text().split()[0]
    assert (
        hashlib.sha256((ROOT / "docs/minimax-h3-license.txt").read_bytes()).hexdigest() == expected
    )


def test_candidate_has_disjoint_4_3_1_placement_and_private_moderation():
    base = yaml.safe_load(render.BASELINE.read_text())
    result = render.render(base, GATEWAY, WORKER)
    assert len(base["containers"]) == 3  # renderer never mutates released baseline
    assert len(result["containers"]) == 4
    c = {c["name"]: c for c in result["containers"]}
    assert "CUDA_VISIBLE_DEVICES=0,1,2,3" in c["vllm-omni-minimax"]["command"][0]
    qwen = c["vllm-omni-qwen"]["command"][0]
    assert "CUDA_VISIBLE_DEVICES=4,5,6 exec" in qwen
    assert '"num_replicas": 1, "devices": "1"' in qwen
    assert '"num_replicas": 1, "devices": "2"' in qwen
    assert ">/dev/null 2>&1" in qwen
    worker = c["moderation-worker"]
    assert "CUDA_VISIBLE_DEVICES=7 exec" in worker["command"][0]
    assert "TRITON_CACHE_DIR=/var/cache/moderation/triton " in worker["command"][0]
    assert worker["tmpfs"] == {
        "/tmp": "size=2g,mode=1777,noexec",
        "/root": "size=64m,mode=0700,noexec",
        "/var/cache/moderation": "rw,size=256m,mode=0700,exec,nosuid,nodev",
    }
    assert worker["networks"] == ["models"]
    # The shim may boot while moderation is unavailable; video admission remains gated.
    assert "/health/liveliness" in worker["healthcheck"]["test"][-1]
    assert "secrets" not in worker and "ports" not in worker and "env" not in worker
    assert result["models"] == base["models"]  # existing five-disk hardware profile
    assert result["shim"] == base["shim"]  # no public worker route
    assert result["networks"] == base["networks"]
    assert worker["image"] == WORKER and c["litellm"]["image"] == GATEWAY
    assert c["litellm"]["env"] == [
        "CCS_MODERATION_PRE_ENABLED",
        "CCS_MODERATION_POST_ENABLED",
    ]


@pytest.mark.parametrize(
    "value",
    ["latest", GATEWAY.replace("@sha256:", ":"), GATEWAY.replace("ccs-images-public", "other")],
)
def test_renderer_rejects_tags_and_other_repositories(value):
    with pytest.raises(ValueError):
        render.render(yaml.safe_load(render.BASELINE.read_text()), value, WORKER)


def test_renderer_refuses_unexpected_baseline():
    base = yaml.safe_load(render.BASELINE.read_text())
    base["containers"][1]["command"] = ["different server"]
    with pytest.raises(ValueError):
        render.render(base, GATEWAY, WORKER)


def test_release_runtime_matches_reviewed_renderer():
    released = yaml.safe_load((ROOT / "tinfoil-config.yml").read_text())
    containers = {c["name"]: c for c in released["containers"]}
    image = containers["vllm-omni-minimax"]["image"]
    if image.startswith(render.REGISTRY + "confidential-qwen-minimax-fasth3@"):
        trial_spec = importlib.util.spec_from_file_location(
            "trial", ROOT / "scripts/render_fasth3_trial.py"
        )
        trial = importlib.util.module_from_spec(trial_spec)
        trial_spec.loader.exec_module(trial)
        baseline = yaml.safe_load((ROOT / "runtime-baselines/v0.5.1.yml").read_text())
        assert released == trial.render(baseline, image)
        released = baseline
        containers = {c["name"]: c for c in released["containers"]}
    minimax = next(m for m in released["models"] if m["name"] == "minimax-h3-fl2va")
    assert released == render.render(
        yaml.safe_load(render.BASELINE.read_text()),
        containers["litellm"]["image"],
        containers["moderation-worker"]["image"],
        minimax_image=containers["vllm-omni-minimax"]["image"],
        minimax_pack=minimax["mpk"],
        minimax_repo=minimax["repo"],
        voice_samples="CCS_MODERATION_AUDIO_ENABLED" in containers["litellm"]["env"],
        watermark_control="CCS_MINIMAX_WATERMARK_ENABLED"
        in containers["vllm-omni-minimax"].get("env", []),
    )


def test_combined_model_stays_on_existing_gpus_and_two_model_disks():
    base = yaml.safe_load(render.BASELINE.read_text())
    image = render.REGISTRY + "confidential-qwen-minimax-video-controls@sha256:" + "c" * 64
    pack = "d" * 64 + "_4096_12345678-1234-1234-1234-123456789012"
    source = "MiniMaxAI/MiniMax-H3@" + "e" * 40
    result = render.render(
        base, GATEWAY, WORKER, minimax_image=image, minimax_pack=pack, minimax_repo=source
    )
    assert len(result["models"]) == 2 and result["gpus"] == 8
    assert result["models"][0]["mpk"] == pack
    assert result["models"][0]["repo"] == source
    assert result["models"][1] == base["models"][1]
    minimax = next(c for c in result["containers"] if c["name"] == "vllm-omni-minimax")
    command = minimax["command"][0]
    assert "CUDA_VISIBLE_DEVICES=0,1,2,3" in command
    assert f"/tinfoil/mpk/mpk-{'d' * 64} " in command and "/FL2VA" not in command
    assert "--tensor-parallel-size=4" in command and "--text-encoder-tp-size=4" in command
    assert "--usp=1" in command and "--vae-patch-parallel-size=4" in command
    assert minimax["image"] == image
    assert base["models"][0]["mpk"] != pack


def test_combined_model_requires_all_verified_artifacts():
    with pytest.raises(ValueError, match="combined H3 requires"):
        render.render(
            yaml.safe_load(render.BASELINE.read_text()), GATEWAY, WORKER, minimax_pack="x"
        )


def test_voice_samples_expose_independent_audio_control_without_gpu_or_model_changes():
    base = yaml.safe_load(render.BASELINE.read_text())
    existing = render.render(base, GATEWAY, WORKER)
    candidate = render.render(base, GATEWAY, WORKER, voice_samples=True)
    gateway = next(c for c in candidate["containers"] if c["name"] == "litellm")
    assert gateway["env"].pop() == "CCS_MODERATION_AUDIO_ENABLED"
    assert candidate == existing


def test_watermark_control_only_exposes_operator_setting_to_minimax():
    base = yaml.safe_load(render.BASELINE.read_text())
    kwargs = {
        "minimax_image": render.REGISTRY
        + "confidential-qwen-minimax-video-controls@sha256:"
        + "c" * 64,
        "minimax_pack": "d" * 64 + "_4096_12345678-1234-1234-1234-123456789012",
        "minimax_repo": "MiniMaxAI/MiniMax-H3@" + "e" * 40,
    }
    existing = render.render(base, GATEWAY, WORKER, **kwargs)
    candidate = render.render(base, GATEWAY, WORKER, **kwargs, watermark_control=True)
    minimax = next(c for c in candidate["containers"] if c["name"] == "vllm-omni-minimax")
    assert minimax["env"].pop() == "CCS_MINIMAX_WATERMARK_ENABLED"
    assert candidate == existing


def test_watermark_control_requires_controls_image():
    with pytest.raises(ValueError, match="watermark control requires"):
        render.render(
            yaml.safe_load(render.BASELINE.read_text()), GATEWAY, WORKER, watermark_control=True
        )
