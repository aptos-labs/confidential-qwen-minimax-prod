import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


diagnostic_installer = load("install_diagnostics")
kernel_installer = load("install_kernel")
diagnostics = load("ccs_fasth3_diagnostics")
installer = load("install")
launcher = load("serve")
controls = load("ccs_fasth3_controls")


def pytest_addoption(parser):
    parser.addoption("--upstream")
    parser.addoption("--kernel-wheel")


@pytest.fixture
def upstream(pytestconfig):
    root = pytestconfig.getoption("--upstream")
    if not root:
        pytest.skip("exact source supplied by the image build")
    return Path(root)
