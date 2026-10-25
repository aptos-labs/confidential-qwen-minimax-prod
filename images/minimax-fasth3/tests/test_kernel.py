import hashlib
import subprocess
import zipfile
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

import pytest
from conftest import kernel_installer as repair


def test_real_pinned_wheel_rebinds_only_driver_name(pytestconfig):
    wheel = pytestconfig.getoption("--kernel-wheel")
    if wheel is None:
        pytest.skip("source wheel supplied for local binary validation")
    with zipfile.ZipFile(wheel) as archive:
        original = archive.read(repair.TARGET)
    patched = repair.patched(original)
    assert len(patched) == len(original)
    offset = original.index(repair.BUNDLED_DRIVER.encode() + b"\0")
    end = offset + len(repair.BUNDLED_DRIVER) + 1
    assert patched[:offset] == original[:offset]
    assert patched[end:] == original[end:]
    assert patched[offset:end].rstrip(b"\0") == b"libcuda.so.1"
    assert hashlib.sha256(patched).hexdigest() == repair.PATCHED_SHA256
    with pytest.raises(ValueError, match="unexpected"):
        repair.patched(original + b"\0")


def test_installed_image_links_system_driver_without_loading_cuda(upstream):
    try:
        package = distribution("fastvideo-kernel")
    except PackageNotFoundError:
        pytest.skip("native dependency is installed in the Linux image build")
    library = Path(str(package.locate_file(repair.TARGET)))
    assert hashlib.sha256(library.read_bytes()).hexdigest() == repair.PATCHED_SHA256
    assert not Path(
        str(package.locate_file("fastvideo_kernel.libs/" + repair.BUNDLED_DRIVER))
    ).exists()
    # ldd reads ELF dependencies on the CPU builder; it does not import the
    # Python module or initialize CUDA. The host driver is absent until boot.
    result = subprocess.run(["ldd", str(library)], capture_output=True, text=True, check=True)
    assert "libcuda.so.1" in result.stdout
    assert "libcuda-" not in result.stdout
