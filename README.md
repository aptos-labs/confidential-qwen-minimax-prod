# Confidential Qwen3-Omni + MiniMax-H3 production runtime

This repository is the public, signed Tinfoil release provenance for the production
dual-model confidential-inference CVM on `host0.inference.aptoslabs.com`:

| Model ID | Model | Container | GPUs |
|---|---|---|---|
| `qwen3-omni` | Qwen3-Omni-30B-A3B-Instruct (chat/omni and moderation) | `vllm-omni-qwen:8000` | 4–6 |
| `minimax-h3-fl2va` | MiniMax-H3 FL2VA (synchronous video) | `vllm-omni-minimax:8001` | 0–3 |
| Private worker | Qwen3Guard-Gen-0.6B | `moderation-worker:8090` | 7 |

The repository name `aptos-labs/confidential-qwen-minimax-prod` was restored for this
deployment. Releases through `v0.0.14` remain in
`aptos-labs/confidential-qwen-minimax-prod-archive`: `v0.0.12` is the last signed MiniMax
runtime, and `v0.0.13`–`v0.0.14` replaced it with Cosmos3-Super. This repository's next
release is a new tag. Do not reuse an archived tag.

## What is attested

`tinfoil-config.yml` is the exact measured runtime the CVM host agent materializes for the
fleet object. Release `v0.3.0` adds mandatory MiniMax input/output moderation, the private
Guard worker, and a 4/3/1 GPU allocation. Model images and packs, hardware shape, and public
API routes are preserved. This release is for initial live validation; moderation quality
and latency on GPUs have not yet been established.
The YAML uses the canonical order emitted after the fleet API's JSON round trip. Its SHA-256 is
`733c69c10db82f01f827546cd72633e16d930f8c1091741cf1e71dc9c4cc576f`.

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

- [MiniMax moderation](docs/moderation.md) describes the implemented worker, mandatory
  gateway checks, 4/3/1 GPU runtime renderer, tests, and staging/release requirements.
  The immutable unmoderated baseline is in `runtime-baselines/v0.2.1.yml`; the current
  `tinfoil-config.yml` contains the moderated release candidate.

- `images/paid-gateway/` contains the gateway, its baked LiteLLM config, entrypoint, and
  offline tests. Its model catalog, backend target, and multipart validation are
  for MiniMax, including `extra_params.task=t2va` and audio generation parameters.
- `images/qwen-metered/` installs the hash-checked vLLM-Omni meter patch at image build
  time. This release pins the verified GAR gateway and worker images below and the existing
  `ghcr.io/aptos-labs/confidential-qwen-minimax-qwen-metered` digest. A rebuilt image
  requires its own verification and a new runtime release.

## Moderation images for v0.3.0

Both images were built from `70b8e27d0f198cc9307e551f539b8565bdf39001`.

| Image | Cloud Build | Digest |
|---|---|---|
| `us-docker.pkg.dev/aptos-registry/ccs-images-public/confidential-qwen-minimax-paid-gateway` | [1370a67b-65ab-4197-8996-b963dc17bc93](https://console.cloud.google.com/cloud-build/builds/1370a67b-65ab-4197-8996-b963dc17bc93?project=352118355028) | `sha256:a6b1c77a7adb5447ed53dcbcae73de6533641c034537ae0e65c07454bf7f1e52` |
| `us-docker.pkg.dev/aptos-registry/ccs-images-public/confidential-qwen-minimax-moderation-worker` | [30064c8f-45fe-4f75-aa1f-716dff6a0e8b](https://console.cloud.google.com/cloud-build/builds/30064c8f-45fe-4f75-aa1f-716dff6a0e8b?project=352118355028) | `sha256:006cd030837b377c7d8918895f0b77bc18832906d9c34f6704972c5719e22218` |

Both builds passed Dockerfile tests, offline smoke, anonymous exact-digest pulls, and
published-image smoke. Independent registry checks verified linux/amd64 and source labels.
Google-signed DSSE provenance was verified against the build IDs, source archive SHA256
`5b27b5021e649b69621a8bf2e0adcca29bd187f2a532f6cef3e0fe1959d89ef8`, source commit and output
digests. Provenance occurrences are stored in Container Analysis in `aptos-cloudbuild`,
separately from the registry project. These CPU checks do not measure GPU inference quality
or performance. The gateway coordinates moderation calls; it does not need a GPU.

## Historical gateway build for v0.2.1

- Source: `63794c53616d32005298b48f8c694e832fd6b218`.
- Cloud Build: [`621f1930-7fff-4f20-a984-5ed4273aa5a5`](https://console.cloud.google.com/cloud-build/builds/621f1930-7fff-4f20-a984-5ed4273aa5a5?project=aptos-cloudbuild).
- Image: `us-docker.pkg.dev/aptos-registry/ccs-images-public/confidential-qwen-minimax-paid-gateway@sha256:a8e08db47e504fc01aee0c56ff8061c8f94ce7c4685d67d8e5676531aff51873`.

The build runs the Dockerfile tests, isolated smoke, anonymous digest pull, and
published-image smoke. Cloud Build provides the source archive and image provenance.

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
   Docker CLI and BuildKit are pinned by digest; Buildx supplies the Dockerfile
   features that the default Cloud Build Docker builder does not support.
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
