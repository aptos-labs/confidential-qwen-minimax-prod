"""Apply limits in a fresh process before exec; never use preexec_fn with threads."""

import os
import resource
import sys


def main() -> None:
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024**2, 64 * 1024**2))
    # Linux production enforces address-space limits. Darwin rejects RLIMIT_AS;
    # local decoder tests retain CPU/file limits and the parent's hard timeout.
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3, 4 * 1024**3))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    os.execv(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
