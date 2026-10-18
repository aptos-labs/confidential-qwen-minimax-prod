# Confidential Qwen3-Omni + MiniMax-H3 production runtime

This repository is the public, signed Tinfoil release provenance for the production
dual-model confidential-inference CVM on `host0.inference.aptoslabs.com`:

| Model ID | Model | Container | GPUs |
|---|---|---|---|
| `qwen3-omni` | Qwen3-Omni-30B-A3B-Instruct (chat/omni) | `vllm-omni-qwen:8000` | 4–7 |
| `minimax-h3-fl2va` | MiniMax-H3 FL2VA (synchronous video) | `vllm-omni-minimax:8001` | 0–3 |

The repository name `aptos-labs/confidential-qwen-minimax-prod` was restored for this
deployment. Releases through `v0.0.14` remain in
`aptos-labs/confidential-qwen-minimax-prod-archive`: `v0.0.12` is the last signed MiniMax
runtime, and `v0.0.13`–`v0.0.14` replaced it with Cosmos3-Super. This repository's next
release is a new tag. Do not reuse an archived tag.

## What is attested

`tinfoil-config.yml` is the exact measured runtime the CVM host agent materializes for the
fleet object. It preserves the image digests, model-pack references, GPU split, network
policy, and API surface of archived release `v0.0.12`, in the canonical YAML order emitted
after the fleet API's JSON round trip. Its SHA-256 is
`adf7632800b140e29eab2f48efca224ceb9a6b6effda0e45b50f08b76b3c9a5a`.

A paid gateway fronts both models. It admits requests through CCS before calling a model
server. Qwen is a chat/audio model. MiniMax is a synchronous video endpoint,
`POST /v1/videos/sync`, and returns `video/mp4`.

The CVM uses Tinfoil's signed `extra_large_2d` hardware profile
([`tinfoilsh/hardware-measurements`](https://github.com/tinfoilsh/hardware-measurements)):
32 vCPUs, 512 GiB RAM, eight H200s with four NVSwitch ports, and five disks (root, measured
config, external config, and two model volumes).

## Sources

- `images/paid-gateway/` contains the gateway, its baked LiteLLM config, entrypoint, and
  offline tests.
- `images/qwen-metered/` installs the hash-checked vLLM-Omni meter patch at image build
  time. This release pins the already-published
  `ghcr.io/aptos-labs/confidential-qwen-minimax-paid-gateway` and
  `ghcr.io/aptos-labs/confidential-qwen-minimax-qwen-metered` digests. A rebuilt image
  requires its own verification and a new runtime release.

## Image publication and verification

1. Review and merge the source PR, inspecting every review thread. PR CI runs policy/script
   tests, both Dockerfile `test` targets, and final-runtime smoke checks with all
   capabilities dropped, read-only roots, and no network.
2. Dispatch **Publish runtime images** on the reviewed `main` commit, passing its full
   40-character hash as `source_sha`.
3. Record the workflow's build-output digests for:
   - `ghcr.io/aptos-labs/confidential-qwen-minimax-paid-gateway`
   - `ghcr.io/aptos-labs/confidential-qwen-minimax-qwen-metered`

   Both packages are already public, so new versions need no visibility change.
4. Verify each image's provenance, for example:

   ```bash
   gh attestation verify "oci://$IMAGE_REF" \
     --repo aptos-labs/confidential-qwen-minimax-prod \
     --signer-workflow aptos-labs/confidential-qwen-minimax-prod/.github/workflows/publish-runtime-images.yml \
     --source-digest "$SOURCE_SHA" --signer-digest "$SOURCE_SHA"
   ```

Image publication never dispatches a Tinfoil runtime release or deploys a VM.

## Runtime release process

1. Export the exact runtime materialized for the fleet CVM:

   ```bash
   cvmctl export-runtime -f qwen-minimax.yaml > tinfoil-config.yml
   ```

2. Review and commit the generated `tinfoil-config.yml`.
3. Run the **Tinfoil Release** workflow with a new, unused immutable tag. Never reuse a tag.
4. Set the fleet object's metadata repository and tag to this repository and release, then
   start it.

Any runtime change (CPU/RAM, a model pack, an image digest, or vLLM arguments) requires a new
release before deployment. Before stopping a running VM, verify that its exported runtime
hash equals the reviewed release. The certificate token and usage secret stay in the
protected external configuration.

## Security boundaries

This repository contains no credentials. The certificate authorization token and
`USAGE_REPORTER_SECRET` exist only in the protected external configuration. The gateway's CCS
destination and reporter identity are fixed in measured code. Missing or invalid reporter
credentials fail startup; unavailable admission fails closed.
