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
    base = yaml.safe_load((ROOT / "tinfoil-config.yml").read_text())
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
    assert worker["networks"] == ["models"]
    assert "secrets" not in worker and "ports" not in worker and "env" not in worker
    assert result["models"] == base["models"]  # existing five-disk hardware profile
    assert result["shim"] == base["shim"]  # no public worker route
    assert result["networks"] == base["networks"]
    assert worker["image"] == WORKER and c["litellm"]["image"] == GATEWAY


@pytest.mark.parametrize(
    "value",
    ["latest", GATEWAY.replace("@sha256:", ":"), GATEWAY.replace("ccs-images-public", "other")],
)
def test_renderer_rejects_tags_and_other_repositories(value):
    with pytest.raises(ValueError):
        render.render(yaml.safe_load((ROOT / "tinfoil-config.yml").read_text()), value, WORKER)


def test_renderer_refuses_unexpected_baseline():
    base = yaml.safe_load((ROOT / "tinfoil-config.yml").read_text())
    base["containers"][1]["command"] = ["different server"]
    with pytest.raises(ValueError):
        render.render(base, GATEWAY, WORKER)
