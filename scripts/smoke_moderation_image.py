"""Check the exact worker image, offline, without GPU or production credentials."""

import argparse
import re
import subprocess

REGISTRY = "us-docker.pkg.dev/aptos-registry/ccs-images-public/confidential-qwen-minimax-moderation-worker"
SCRIPT = r"""
import asyncio,hashlib,json,pathlib
import httpx
from moderation_worker.app import create_app
from moderation_worker.policy import GUARD_REVISION,guard_verdict
root=pathlib.Path('/opt/qwen3guard')
artifact=json.loads((root/'artifact.json').read_text())
assert artifact['revision']==GUARD_REVISION
for name,digest in artifact['sha256'].items():
 assert hashlib.sha256((root/name).read_bytes()).hexdigest()==digest
assert guard_verdict('Safety: Controversial\nCategories: None').decision=='block'
async def smoke():
 async with httpx.AsyncClient(transport=httpx.ASGITransport(create_app()),base_url='http://test') as client:
  assert (await client.get('/health/liveliness')).status_code==200
  assert (await client.get('/health/readiness')).status_code==503
  result=await client.post('/pre',content=b'fixture')
  assert result.status_code==503 and result.json()=={'decision':'block','category':'moderation_unavailable'}
asyncio.run(smoke())
print('Worker artifact verified; GPU-less inference fails closed.')
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("--local", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(re.escape(REGISTRY) + r"@sha256:[0-9a-f]{64}", args.image):
        if not args.local or args.image != "ccs-moderation:smoke":
            parser.error("expected exact worker GAR digest")
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:rw,nosuid,size=256m",
            "--entrypoint",
            "python3",
            args.image,
            "-c",
            SCRIPT,
        ],
        check=True,
        timeout=180,
    )


if __name__ == "__main__":
    main()
