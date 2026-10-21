"""Measured entrypoint; no arguments, development server, access logs or core dumps."""

import logging
import os
import resource
import sys


def main() -> None:
    if len(sys.argv) != 1:
        raise RuntimeError("moderation worker does not accept arguments")
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    logging.disable(logging.CRITICAL)
    import uvicorn

    from .app import create_app

    uvicorn.run(
        create_app(),
        host="0.0.0.0",
        port=8090,
        workers=1,
        access_log=False,
        log_config=None,
        server_header=False,
    )


if __name__ == "__main__":
    main()
