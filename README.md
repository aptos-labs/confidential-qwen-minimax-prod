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
`9bda5d1b8b69b8280cc0b62acb62d01d131e332206a8a527f3edc19085fa4122`.

A gateway fronts both models and attempts CCS admission before calling a model
server. When the admission route returns 404, the gateway uses the same usage-only
fallback as Cosmos: generation proceeds with a 30-minute deadline, and billing
checkpoints and settlement are skipped. Other admission failures remain errors.
This fallback does not establish that generated video is billed.
Qwen is a chat/audio model. MiniMax is a synchronous video endpoint,
`POST /v1/videos/sync`, and returns `video/mp4`.

The CVM uses Tinfoil's signed `extra_large_2d` hardware profile
([`tinfoilsh/hardware-measurements`](https://github.com/tinfoilsh/hardware-measurements)):
32 vCPUs, 512 GiB RAM, eight H200s with four NVSwitch ports, and five disks (root, measured
config, external config, and two model volumes).

## Sources

- `images/paid-gateway/` contains the gateway, its baked LiteLLM config, entrypoint, and
  offline tests. Its model catalog, backend target, and multipart validation are
  for MiniMax, including `extra_params.task=t2va` and audio generation parameters.
- `images/qwen-metered/` installs the hash-checked vLLM-Omni meter patch at image build
  time. This release pins the already-published
  `ghcr.io/aptos-labs/confidential-qwen-minimax-paid-gateway` and
  `ghcr.io/aptos-labs/confidential-qwen-minimax-qwen-metered` digests. A rebuilt image
  requires its own verification and a new runtime release.

## Image publication and verification

1. Review and merge the source PR, inspecting every review thread. PR CI runs policy/script
   tests, both Dockerfile `test` targets, and final-runtime smoke checks with all
   capabilities dropped, read-only roots, and no network.
2. Publish the gateway to public GAR using Cloud Build in `aptos-cloudbuild`:

   ```bash
   SOURCE_SHA=$(git rev-parse HEAD)
   git diff --exit-code
   git merge-base --is-ancestor "$SOURCE_SHA" origin/main
   git archive --format=tar.gz --output=/tmp/minimax-gateway-source.tar.gz "$SOURCE_SHA"
   gcloud builds submit /tmp/minimax-gateway-source.tar.gz \
     --project=aptos-cloudbuild --config=cloudbuild-gateway.yaml \
     --substitutions="_SOURCE_SHA=$SOURCE_SHA"
   ```

   `cloudbuild-gateway.yaml` runs the Dockerfile tests and isolated runtime smoke,
   publishes to
   `us-docker.pkg.dev/aptos-registry/ccs-images-public/confidential-qwen-minimax-paid-gateway`,
   then anonymously pulls and smokes the exact published digest. It uses the existing
   CCS builder service account and requires Cloud Build provenance verification.
   The source archive contains only committed repository files. Credentials and fleet
   external configuration must never be included in the build context.
3. Record the successful build ID, source commit, and resulting digest. Inspect the
   build's `sourceProvenance` and `results.images`, and the registry provenance:

   ```bash
   gcloud builds describe "$BUILD_ID" --project=aptos-cloudbuild
   gcloud artifacts docker images describe "$IMAGE_REF" --show-provenance \
     --project=aptos-registry
   ```

   Require the successful build's source archive, OCI revision label, and image digest
   to match the reviewed commit and release pin. Keep the deployed Qwen digest unchanged
   when releasing a gateway-only fix. The older **Publish runtime images** workflow
   remains the GHCR publication path; it is not used for this GAR gateway release.

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
credentials fail startup. Admission transport errors and server failures fail closed;
only an explicit 404 selects the usage-only fallback described above.
