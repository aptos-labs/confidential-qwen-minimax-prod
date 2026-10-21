"""Build-time only: download an immutable public checkpoint and record file hashes."""

import hashlib
import json
from pathlib import Path

from huggingface_hub import snapshot_download

REPO = "Qwen/Qwen3Guard-Gen-0.6B"
REVISION = "fada3b2f655b89601929198343c94cd2f64d93cc"


def main() -> None:
    target = Path("/opt/qwen3guard")
    snapshot_download(
        REPO,
        revision=REVISION,
        local_dir=target,
        token=False,
        allow_patterns=["*.json", "*.safetensors", "*.txt", "*.jinja", "LICENSE"],
    )
    files = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(target.iterdir())
        if p.is_file()
    }
    if not any(name.endswith(".safetensors") for name in files):
        raise RuntimeError("guard weights missing")
    (target / "artifact.json").write_text(
        json.dumps(
            {
                "repo": REPO,
                "revision": REVISION,
                "sha256": files,
            },
            sort_keys=True,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
