"""CPU tests use an exact, read-only copy of the deployed model image's files."""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ARTIFACT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("minimax_controls_install", ARTIFACT / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def pytest_addoption(parser):
    parser.addoption("--upstream", help="vllm_omni directory in the pinned watermark image")


@pytest.fixture
def package(tmp_path, pytestconfig):
    source = pytestconfig.getoption("--upstream")
    if not source:
        pytest.skip("exact-image tests require --upstream; always supplied in the Docker build")
    manifest = json.loads((ARTIFACT / "manifest.json").read_text())
    root = tmp_path / "vllm_omni"
    for name in (installer.TARGET, *manifest["preserved_files"]):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(source) / name, target)
    return root


@pytest.fixture
def installed(package):
    installer.install(package)
    return package
