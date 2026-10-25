import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("trial", ROOT / "scripts/render_fasth3_trial.py")
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


def test_trial_changes_only_minimax_image_command_and_private_cache():
    base = yaml.safe_load((ROOT / "tinfoil-config.yml").read_text())
    candidate = trial.render(base, trial.IMAGE + "@sha256:" + "a" * 64)
    old = next(c for c in base["containers"] if c["name"] == "vllm-omni-minimax")
    new = next(c for c in candidate["containers"] if c["name"] == "vllm-omni-minimax")
    assert "CUDA_VISIBLE_DEVICES=0,1,2,3 " in new["command"][0]
    assert new["command"][0].endswith(">/dev/null 2>&1")
    assert "exec," in new["tmpfs"].pop("/var/cache/fasth3")
    for key in ("image", "command", "entrypoint"):
        new[key] = old[key]
    assert candidate == base


@pytest.mark.parametrize("image", ["latest", trial.IMAGE + ":v1", "other@sha256:" + "a" * 64])
def test_trial_requires_published_digest(image):
    with pytest.raises(ValueError):
        trial.render({}, image)
