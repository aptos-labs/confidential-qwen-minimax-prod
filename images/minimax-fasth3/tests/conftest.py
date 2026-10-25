import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load("install")
launcher = load("serve")


def pytest_addoption(parser):
    parser.addoption("--upstream")


@pytest.fixture
def upstream(pytestconfig):
    root = pytestconfig.getoption("--upstream")
    if not root:
        pytest.skip("exact source supplied by the image build")
    return Path(root)
