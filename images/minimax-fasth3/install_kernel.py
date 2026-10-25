"""Make the pinned native kernel use the CVM's NVIDIA driver.

The cp312 x86_64 fastvideo-kernel 0.3.4 wheel bundles a renamed libcuda 580.
Its SM90 extension links that copy, while PyTorch initializes the injected host
driver. TMA descriptor creation then fails and the kernel faults. Rebind the
one pinned ELF dependency to libcuda.so.1 and remove the unused bundled driver.
"""

from __future__ import annotations

import hashlib
from importlib.metadata import distribution, version
from pathlib import Path

TARGET = "fastvideo_kernel/_C/fastvideo_kernel_ops.cpython-312-x86_64-linux-gnu.so"
ORIGINAL_SHA256 = "e1b703c871e37b273d0000d83de4bfd247f27618693f87ebad59dce689943cdb"
PATCHED_SHA256 = "09a8d89cd9eb455f8b87597c7102c7a5d925178def3caeeb1164be028a90a50f"
BUNDLED_DRIVER = "libcuda-dcdf334a.so.580.178.04"


def patched(source: bytes) -> bytes:
    if hashlib.sha256(source).hexdigest() != ORIGINAL_SHA256:
        raise ValueError("unexpected fastvideo-kernel native binary")
    old = BUNDLED_DRIVER.encode() + b"\0"
    new = b"libcuda.so.1\0"
    if source.count(old) != 1:
        raise ValueError("bundled driver dependency must match exactly once")
    # This audited ELF string-table entry is shared by DT_NEEDED/version
    # metadata. NUL padding preserves every offset, instruction and tensor op.
    result = source.replace(old, new + b"\0" * (len(old) - len(new)), 1)
    if hashlib.sha256(result).hexdigest() != PATCHED_SHA256:
        raise ValueError("unexpected patched native binary")
    return result


def install() -> None:
    if version("fastvideo-kernel") != "0.3.4":
        raise ValueError("native driver repair requires fastvideo-kernel 0.3.4")
    package = distribution("fastvideo-kernel")
    library = Path(str(package.locate_file(TARGET)))
    driver = Path(str(package.locate_file("fastvideo_kernel.libs/" + BUNDLED_DRIVER)))
    if library.is_symlink() or driver.is_symlink():
        raise ValueError("kernel and bundled driver must be regular wheel files")
    result = patched(library.read_bytes())
    if not driver.is_file():
        raise ValueError("expected bundled CUDA driver is missing")
    library.write_bytes(result)
    driver.unlink()
