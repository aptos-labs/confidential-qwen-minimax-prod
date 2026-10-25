# Confidential Qwen3-Omni + MiniMax-H3 production runtime

## Temporary FastH3 trial: v0.6.0-rc.1

This branch publishes a temporary T2VA-only trial on the existing four H200s.
Its `tinfoil-config.yml` replaces the MiniMax image, launch command and private
compile cache. Qwen, Guard, the gateway and both model volumes are unchanged.
Requests use `num_inference_steps=4` without flow-shift overrides. FL2VA, Ref2VA
and voice-reference requests are unavailable during this trial.

Restore the exact signed v0.5.1 deployment after benchmarking. Its configuration
is preserved in `runtime-baselines/v0.5.1.yml`; the sections below describe that
full-service baseline. Do not merge this trial configuration into main.

Cloud Build `11cbe341-86e4-4ec4-b220-599d610cb7c4` published the public GAR image
`confidential-qwen-minimax-fasth3@sha256:8a2ea5cb387411eadec038b3a8d3b9c4acbac3b4c7d0209f35c4108724288812`
from source `0e3e633a0c760a3c5c2fd731ae153d809391f07f`. The image digest, anonymous
access and Google-signed source provenance were independently verified.
Eleven exact-source serving/media tests and 41 native FastH3 tests passed.
GPU boot, quality and performance are separate trial checks. See
[FastH3 trial details](docs/fasth3.md).

This repository is the public, signed Tinfoil release provenance for the production
dual-model confidential-inference CVM on `host0.inference.aptoslabs.com`:

| Model ID | Model | Container | GPUs |
|---|---|---|---|
| `qwen3-omni` | Qwen3-Omni-30B-A3B-Instruct (chat/omni and moderation) | `vllm-omni-qwen:8000` | 4–6 |
| `minimax-h3-fl2va` | MiniMax-H3 FL2VA + Ref2VA (synchronous video) | `vllm-omni-minimax:8001` | 0–3 |
| Private worker | Qwen3Guard-Gen-0.6B | `moderation-worker:8090` | 7 |

The repository name `aptos-labs/confidential-qwen-minimax-prod` was restored for this
deployment. Releases through `v0.0.14` remain in
`aptos-labs/confidential-qwen-minimax-prod-archive`: `v0.0.12` is the last signed MiniMax
runtime, and `v0.0.13`–`v0.0.14` replaced it with Cosmos3-Super. This repository's next
release is a new tag. Do not reuse an archived tag.

## What is attested

`tinfoil-config.yml` is the exact measured runtime the CVM host agent materializes for the
fleet object. Release `v0.5.1` adds operator control of the visible MiniMax watermark.
The requested deployment sets `CCS_MINIMAX_WATERMARK_ENABLED=false`; omitted/true retains
its original overlay. MiniMax shards its combined checkpoint over GPUs 0–3; Qwen remains on
4–6 and Guard on 7. The signed hardware shape, two model volumes, voice references,
silent delivery and configurable moderation stages are preserved. The deployment keeps prompt/image checks enabled, with voice-sample content
classification and output checks disabled. Format and duration validation remain enforced. Broader moderation quality and load performance remain unvalidated.
The YAML uses the canonical order emitted after the fleet API's JSON round trip. Its SHA-256 is
`c93a9af17d13c8efc375ab975230cc18ad85ef3494ee20ee1f1e3ea2381f2651`.

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

- [Video controls](docs/video-controls.md) covers Ref2VA and silent output,
  its combined checkpoint, exact-source model patch, and negative-guidance limitation.

- [MiniMax moderation](docs/moderation.md) describes the implemented worker, mandatory
  gateway checks, 4/3/1 GPU runtime renderer, tests, and staging/release requirements.
  The immutable unmoderated baseline is in `runtime-baselines/v0.2.1.yml`; the current
  `tinfoil-config.yml` contains the measured moderated runtime.

- `images/paid-gateway/` contains the gateway, its baked LiteLLM config, entrypoint, and
  offline tests. Its model catalog, backend target, and multipart validation are
  for MiniMax, including `extra_params.task=t2va` and audio generation parameters.
- `images/qwen-metered/` installs the hash-checked vLLM-Omni meter patch at image build
  time. This release pins the verified GAR gateway and worker images below and the existing
  `ghcr.io/aptos-labs/confidential-qwen-minimax-qwen-metered` digest. A rebuilt image
  requires its own verification and a new runtime release.

## Visible watermark control in v0.5.1

The measured runtime replaces only the MiniMax controls image and exposes the
operator-owned `CCS_MINIMAX_WATERMARK_ENABLED` startup setting to that container. The
requested external value is `false`, which skips the visible overlay before encoding.
Omitted/`true` retains the original notice; invalid values fail at module startup.
See [watermark configuration](docs/video-controls.md#visible-watermark).

Gateway, worker and Qwen image pins, model packs, GPU placement and moderation settings
are unchanged. The merged Guard 16K source change is still pending a worker rollout;
this release retains the worker's 8,192-token input limit.

Model-image source: `6fab1fcac778138e124226966d5d9ba986545668`, reviewed in
[#20](https://github.com/aptos-labs/confidential-qwen-minimax-prod/pull/20).
Cloud Build [9427b6c6-8665-4268-94a1-600b28427758](https://console.cloud.google.com/cloud-build/builds/9427b6c6-8665-4268-94a1-600b28427758?project=aptos-cloudbuild)
published `us-docker.pkg.dev/aptos-registry/ccs-images-public/confidential-qwen-minimax-video-controls@sha256:4bced0f5a20464024bae7a54db46a03a855e37c2d91042d03f3aedba4cb04246`. Source archive SHA-256: `83167fee7bb6decd6f2f7b9d77261d9d5438f026562778f2ddbd1061c136e40c`.
Source hashes, signed build provenance and anonymous GAR access were independently verified.
Local validation passed 696 tests plus 47 subtests, 43 exact-source image tests, Ruff and
strict mypy. The final-image CPU checks cover watermark default/on/off and invalid values;
live boot and unwatermarked video remain rollout checks.

## Voice samples in v0.5.0

The measured runtime replaces only the gateway and worker images and exposes
`CCS_MODERATION_AUDIO_ENABLED` (default `false`). WAV/MP3 references use the existing H3
image and combined checkpoint; model artifacts, watermark and 4/3/1 GPU placement are retained.
The requested external configuration is pre=`true`, post=`false`, audio=`false`.
See [voice samples](docs/video-controls.md#voice-samples) for the curl example and limits.

Gateway/worker source: `e50ad740d93d2fc50dd26841bf796533e94331b6`, reviewed and merged in
[#17](https://github.com/aptos-labs/confidential-qwen-minimax-prod/pull/17) as
`b379c1c8645b2de8b744c4b2b770af2a5a5cacd2` with an identical tree. Local tests (691 plus
47 subtests), strict mypy, Ruff, all CI image checks and Bugbot passed. Cloud Build tested
and published both images to `us-docker.pkg.dev/aptos-registry/ccs-images-public`; signed
provenance, source archive hashes, OCI digests and anonymous pull access were independently verified.

| Image | Cloud Build | Digest |
|---|---|---|
| `confidential-qwen-minimax-paid-gateway` | [5c7786fd-fde8-41b5-82a5-6759ec14b2c9](https://console.cloud.google.com/cloud-build/builds/5c7786fd-fde8-41b5-82a5-6759ec14b2c9?project=aptos-cloudbuild) | `sha256:b957cb568e0eda20efc9c8321f0dbe8ff0ada9b1cd19b60707152baf13c0d2b2` |
| `confidential-qwen-minimax-moderation-worker` | [6054f6f7-b05f-466b-b3eb-d08da7e5b321](https://console.cloud.google.com/cloud-build/builds/6054f6f7-b05f-466b-b3eb-d08da7e5b321?project=aptos-cloudbuild) | `sha256:856f4b56e57407f3edf56d764ebe5535741f5434674a7912adf5b3b847eb73bb` |

Source archive SHA-256: `df5ddcdd61d51da908198829059be2b7ebd43d8b5fe421593600bf9d60ea6822`.
Live voice fidelity, GPU latency and deployment readiness are separate rollout checks.

## Reference images and silent output in v0.4.0

Select `extra_params.task=ref2va` and upload 1–9 ordered `input_references` multipart parts.
Set `generate_sound=false` for an MP4 with no audio track. H3 still computes joint audio/video
latents. Native `negative_prompt` guidance is unsupported; prompt rewriting is not enabled.
Usage and character-reference syntax are in [video controls](docs/video-controls.md).

Gateway and worker source: `c249ac4ccf77f9a5a82b7d689c0bbb6cdee3b296` ([#14](https://github.com/aptos-labs/confidential-qwen-minimax-prod/pull/14)).
H3 controls source: `5eb615b961cf4a502cf194a89143032f612c7a79` ([#15](https://github.com/aptos-labs/confidential-qwen-minimax-prod/pull/15)),
including the compatibility fix that preserves audio when `generate_sound` is omitted.

| Image | Cloud Build | Digest |
|---|---|---|
| `confidential-qwen-minimax-paid-gateway` | [22ca1f52-c07a-4811-9f3c-7d46150eb744](https://console.cloud.google.com/cloud-build/builds/22ca1f52-c07a-4811-9f3c-7d46150eb744?project=aptos-cloudbuild) | `sha256:3e78cd2ead62bfbdfd4be3aef82a806484f084b75e9266102485c01764cedffb` |
| `confidential-qwen-minimax-moderation-worker` | [249bbc11-a4ef-4502-b5e7-5bf1532f0c2c](https://console.cloud.google.com/cloud-build/builds/249bbc11-a4ef-4502-b5e7-5bf1532f0c2c?project=aptos-cloudbuild) | `sha256:445df99707167e10828f993a027cf29dc3cf7d38a158da01fce00703665fc8a8` |
| `confidential-qwen-minimax-video-controls` | [36ce6d69-4219-40dc-9190-738ed26454dc](https://console.cloud.google.com/cloud-build/builds/36ce6d69-4219-40dc-9190-738ed26454dc?project=aptos-cloudbuild) | `sha256:7a83408219c13ee9139364b6fc460324b5ed7e85b7db6ebc8bf83c8ef1826e93` |

Docker tests, isolated final-image smoke and anonymous published-digest pulls passed.
Independent verification checked Google-signed provenance, OCI revision/platform labels,
and output digests. Source archive SHA-256:
gateway/worker `328f0b634b7d2c175028bf333eae5cfe82ce70304e9c11e7b8e58df95240f3f0`;
H3 controls `0bcad4df08568fb756210e2e1745f9dbe427edf2a83fe2a4b2e9dbcc0aa55f5b`.
The model image preserves the existing `NOT FOR COMMERCIAL USE` watermark.

The [combined model pack](https://storage.googleapis.com/aptos-cvm-images/models/minimax-h3-combined-94337995ea91d9db466c878eb0533c54f7345b38e4db884fef91287d6d6e721e.mpk) was built with Modelwrap v0.3.0 from
`MiniMaxAI/MiniMax-H3@42ed227ee7df40d41602854ae760620d6eb651fe`. Every input matched its pinned Hugging Face Git/LFS hash;
offline dm-verity verification and anonymous object/checksum verification passed.
Pack BLAKE3: `94337995ea91d9db466c878eb0533c54f7345b38e4db884fef91287d6d6e721e`. Measured model reference:
`d202414c2cfe1a5d04643061ef01566f50130a2e914d0cb8ecbc3a0683157b47_210331811840_1cee8566-240e-54b9-860d-01417a9ef424`. Shared encoders/VAEs are retained once; only the Ref2VA DiT is added.
Live GPU boot and rendering are separate deployment checks; CPU image checks do not establish
character fidelity, memory headroom, or latency under load.

## Configurable moderation stages in v0.3.5

`CCS_MODERATION_PRE_ENABLED` and `CCS_MODERATION_POST_ENABLED` independently control
input and output checks. Both default to `true`; only literal `true`/`false` are valid.
These are operator-owned startup settings, with effective values reported by `/health`.
The requested deployment keeps pre-checks enabled and disables post-checks. Authentication,
billing and gateway MP4 validation still apply. See [configuration details](docs/moderation.md#stage-configuration).
The measured runtime exposes both environment names; the deployment's external configuration
sets `CCS_MODERATION_PRE_ENABLED=true` and `CCS_MODERATION_POST_ENABLED=false`. Changing these
startup values needs a gateway restart, but no image rebuild. External values are
operator-controlled rather than included in the runtime hash.

The gateway was built from `abf9c3eb6713643634ec298f631991b3f4826cf2` by
[Cloud Build c44c7b04-f3e2-459a-8160-4964eb898e5f](https://console.cloud.google.com/cloud-build/builds/c44c7b04-f3e2-459a-8160-4964eb898e5f?project=aptos-cloudbuild).
Tests, offline smoke, anonymous digest pulls, and Google-signed provenance verification passed.
Gateway: `us-docker.pkg.dev/aptos-registry/ccs-images-public/confidential-qwen-minimax-paid-gateway@sha256:cca29c407654afc510dee8c91ab35c597873078ef11c27662ddb185327211c42`.
The worker and model image digests are retained from v0.3.4. The runtime change is the gateway
image and its two environment names; the GPU layout and dedicated Guard Triton cache remain
as documented below.

## Guard GPU startup fix for v0.3.4

Guard's CUDA self-test in v0.3.3 failed because PyTorch's eager Qwen3 rotary-embedding
operation loads a Triton-generated shared library from `/root/.triton`, while `/root` is
mounted `noexec`. v0.3.4 sets `TRITON_CACHE_DIR=/var/cache/moderation/triton` and adds a
private 256 MiB executable tmpfs there, with `nosuid,nodev`. `/root` and media scratch
remain `noexec`; the container root remains read-only.

The same published worker image passed its real Guard self-test (`allow / none`) on the
existing H200 CVM with this cache and the production filesystem restrictions. This is a
runtime-only change: both v0.3.3 image digests below are reused. Full production readiness,
moderated video generation, and post-generation latency still require live validation.

## Startup diagnostics for v0.3.3

The live v0.3.2 release reached Qwen and MiniMax readiness, but moderation remained
unavailable. This release adds bounded startup-stage diagnostics to distinguish Guard
initialization from the worker's Omni self-tests. It preserves fail-closed pre/post checks,
GPU placement, model images and packs, and private networking. Live video validation and
latency measurement remain pending.

Both images were built from `ba3fe5c00755c13d0fd9d7c17c09ed5058c39d3e` and published to
`us-docker.pkg.dev/aptos-registry/ccs-images-public`:

| Image | Cloud Build | Digest |
|---|---|---|
| `confidential-qwen-minimax-paid-gateway` | [0fe0385d-5706-45c1-aad1-59dc9ed94128](https://console.cloud.google.com/cloud-build/builds/0fe0385d-5706-45c1-aad1-59dc9ed94128?project=352118355028) | `sha256:32b44879dda148a4f727053f8157cb51bb70bb7ae91ff82048d63f258619ae4f` |
| `confidential-qwen-minimax-moderation-worker` | [f4c07150-e883-40bf-9f7a-6a601099c531](https://console.cloud.google.com/cloud-build/builds/f4c07150-e883-40bf-9f7a-6a601099c531?project=352118355028) | `sha256:a60441af96de9cecf5ec7866513f319c33c7f263b82a925587dda8e1b2ebbf0b` |

Both builds passed tests, offline smoke, and anonymous published-digest pulls. Independent
verification checked the OCI source/platform labels and Google-signed provenance against
source archive SHA-256 `1a472cf6f4bdca078755e59d54348f0f2a8859170f67619d250980cc4cf351d8`.
These checks do not establish live GPU readiness or moderation quality.

## Worker update for v0.3.2

The worker is pinned to `us-docker.pkg.dev/aptos-registry/ccs-images-public/confidential-qwen-minimax-moderation-worker@sha256:fa0948b491ca4b46d2128b592f6a02882176e5863f772fbd779930cf9ae6227a`, built from `a35ba9a251fc082e97f1ac2b986854e7cf3136a3` by
[Cloud Build 106a35bb-9fac-4580-a1f2-ae22b1e07a44](https://console.cloud.google.com/cloud-build/builds/106a35bb-9fac-4580-a1f2-ae22b1e07a44?project=352118355028).
The gateway image below is reused. Tests, the real-model CPU adapter self-test, anonymous
published-digest pull and smoke passed. Google-signed provenance binds the reviewed source
archive (`e45027657c9c7a506b42cf2f54701d4ade80110f14f786be32de265d802bd680`) to this digest.

This fixes the Transformers 5 tokenizer return-type mismatch that prevented Guard readiness
in v0.3.1. Worker boot liveness now permits the shim and Qwen to start while moderation is
unavailable; MiniMax checks remain fail closed. Live GPU validation and timing are pending.

## Moderation images for v0.3.0 and v0.3.1

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

v0.3.1 removes the unsupported `--disable-log-requests` Qwen startup option introduced in
v0.3.0. The exact pinned image contains vLLM 0.28.0 with request logging disabled by default;
backend stdout/stderr suppression remains in place. Image pins, GPU placement and moderation
behavior are unchanged. [CPU image inspection](https://console.cloud.google.com/cloud-build/builds/0bd5fd4c-a51b-4704-b8c6-91578eac1bf5?project=352118355028)
confirmed the installed versions, logging default and absence of that option. The CPU CLI
help command could not initialize its platform without a GPU, so live validation is still
required.

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
