# FastH3 on four H200s: parity requirements

Status: the v0.6.0-rc.1 failure was reproduced and traced to a bundled CUDA driver
in the native kernel wheel. The driver repair passes full T2VA generation on four
H200s in an isolated debug CVM. A permanent migration must preserve all currently
working modes and controls; this remains a temporary T2VA trial.

## v0.6.0-rc.1 trial result

The signed candidate booted successfully on four H200s, including FastH3 model
health, trusted TLS, Qwen and Guard. The image passed 11 serving/media tests and
41 upstream fusion/schedule tests before publication. Its digest is
`sha256:8a2ea5cb387411eadec038b3a8d3b9c4acbac3b4c7d0209f35c4108724288812`.

Generation did not succeed. With the same cats prompt, seed 1101, 1344×768 and
four inference steps, the five-second warm-up returned HTTP 502 `upstream_failed`
after 25.47 seconds; the 15-second request returned the same error after 1.98
seconds. Both incremented MiniMax's aborted-request counter. Backend health
remained successful and no video was returned. These are failure latencies,
not generation-performance results.

The gateway suppresses upstream exception bodies, and this production runtime
discards model stdout/stderr with guest SSH and console disabled. The underlying
exception was therefore not captured. Do not infer an OOM, kernel, scheduling or
adapter-fusion cause from the HTTP error. A subsequent trial needs diagnostics
that expose the failure without retaining request content.

The diagnostic candidate retains the same weights, kernel version and GPU
configuration. It instruments exception handlers in six pinned upstream modules
(API, runner, worker, executor, H3 pipeline and H3 VSA), including capture before
worker cleanup erases tracebacks. Each process/boundary keeps only its most recent
failure in private ephemeral storage. The existing metrics response gains
`ccs_fasth3_failure_location`, with fixed exception/reason categories and at most
12 build-time source-file IDs/line numbers. It never retains exception text,
source snippets, locals, request IDs, prompts or media. Unknown/JIT source paths
are omitted. Diagnostic I/O failure leaves the original exception unchanged.
The snapshot is diagnostic evidence, not a request counter or readiness signal.

The isolated debug CVM reproduced the failure using the exact rc.1 image:
HTTP 500 after 26.09 seconds, with TMA descriptor initialization errors followed
by a CUDA illegal-instruction fault. The original gateway mapped that failure
to HTTP 502. A standalone 128-token SM90 attention test reproduced the same
failure in `block_sparse_h100.cu:873`; no H3 weights or request parser were involved.

The cp312 x86_64 fastvideo-kernel 0.3.4 wheel bundles
`libcuda-dcdf334a.so.580.178.04`, and its native extension links that private driver
instead of the system `libcuda.so.1` initialized by PyTorch. The debug CVM uses
NVIDIA driver 595.71.05. Rebinding the native extension to the system driver makes
the standalone native test pass with Triton forcing disabled. The fixed package
removes the unused bundled driver and retains the native SM90 provider. This
matters because the package automatically selects SM90 on H200 even though the
vLLM wrapper describes that branch as a Triton route.

`install_kernel.py` repairs only the known wheel binary (SHA-256
`e1b703c871e37b273d0000d83de4bfd247f27618693f87ebad59dce689943cdb`). It changes one
ELF string-table dependency to `libcuda.so.1`, preserves all offsets/instructions,
and verifies the resulting SHA-256
`09a8d89cd9eb455f8b87597c7102c7a5d925178def3caeeb1164be028a90a50f`.
Source drift fails the build. CPU checks validate both the pinned binary change
and the installed ELF dependency without importing CUDA on the builder.

Forcing `FASTVIDEO_VSA_TRITON=1` is a separately verified fallback: a full
five-second T2VA request returned HTTP 200 in 43.10 seconds cold, with a fully
decodable 1344×768/24 FPS H.264 video and stereo AAC (5.175 seconds encoded).
The repaired native kernel also passed full H3 generation: the cold five-second
request took 31.46 seconds, followed by two 15-second requests in 25.99 and 25.53
seconds. The outputs decode completely as 1344×768 at 24 FPS, with stereo AAC;
the longer clips are exactly 15.000 seconds. All four worker process maps show
the repaired kernel and only the system 595.71.05 CUDA driver. These timings are
HTTP requests inside the isolated backend, excluding public gateway/moderation
and client-network overhead. The 10-second end-to-end target is not met.

The exact v0.5.1 deployment was restored after these two failures. Its live spec
and runtime hash matched the original snapshot. Trusted health passed for all
backends with pre-moderation enabled and post/audio moderation disabled. A
first/last-frame FL2VA request with silent delivery returned HTTP 200 and a valid
1344×768, 24 FPS H.264 MP4 without audio (5.166667 seconds encoded for the requested
five seconds), in 133.57 seconds end to end. Unauthenticated requests returned
HTTP 401. Host-agent and host-daemon process/start identities and all other VM
process IDs remained unchanged throughout the trial and rollback.

The original v0.5.1 baseline request succeeded: 15.083333 seconds of 1344×768 video
at 24 FPS with stereo AAC, 445.15 seconds measured inside MiniMax and 456.15 seconds
for the authenticated HTTP request including download. The 10-second target has
not been established.

## Temporary vLLM-Omni trial

The candidate uses vLLM/vLLM-Omni v0.30.0, not the FastVideo server. This stable
release supports the VSA adapter; the earlier Dense-only restriction belongs to
our v0.28.0 baseline. The new image is `confidential-qwen-minimax-fasth3` in the
public GAR repository. All image references in a rendered runtime require digests.

Pinned inputs:

- vLLM-Omni source: `a8576ccb725c4e21cd13c3eb5f9a546b21149d2b` (v0.30.0).
- vLLM amd64 base: `docker.io/vllm/vllm-openai@sha256:5f5e535216848d0c52159c8c13a0af04be5f6fe1a84e79914300610796f76d40`.
- FastH3 adapter repository revision: `f509e629374cac104e7f62daecce6d1488a3041d`.
- Adapter: `vsa-datafree/adapter_model.safetensors`, 5,339,117,712 bytes,
  SHA-256 `42dc502a2078f166c396a1fa75f29728d1844363652d345d5ef3e2b444ed6470`.
- VSA kernels: `fastvideo-kernel==0.3.4`.

The adapter is included in the immutable image. Both existing measured model
packs are retained. The launcher selects only the FL2VA partition (which supplies
T2VA), TP4, encoder TP4, USP1, VAE parallelism 4, and `FASTVIDEO_VSA`. This placement
is a test candidate, not an established four-H200 performance result. No model
download occurs inside the CVM. A private executable tmpfs holds compile caches.

The trial accepts T2VA with `num_inference_steps=4`; do not carry the old 50-step
request or explicit video/audio flow-shift overrides into this experiment.
FastVideo's five-grid-point API is different from vLLM-Omni's four-forward API.
The adapter is fused at startup and cannot be disabled per request. FL2VA and
Ref2VA remain unavailable during this temporary trial, so v0.5.1 must be restored
after benchmarking. Do not advertise the trial as a full-service upgrade.

The gateway, Qwen, Guard, moderation settings and billing remain unchanged. The
image preserves explicit silent delivery. Upstream does not add our old visible
overlay; startup requires `CCS_MINIMAX_WATERMARK_ENABLED=false`. Watermark-on is
not implemented in this trial. Runtime stdout/stderr suppression is retained.

The trial caps decoded output at 360 frames and 15 seconds of audio before MP4
encoding. Native H3 rounds 15-second requests to 362 frames; returning those
unchanged would exceed the gateway's existing 15-second billing cap. Shorter
clips are unaffected. Exact-source CPU encoding tests verify full 15-second
video and audio tracks that do not exceed this cap.

Build with `cloudbuild-fasth3.yaml`. Before publication, Cloud Build runs our
exact-source serving/MP4 tests, upstream's CPU adapter-fusion tests, and an offline
final-image controls import and package check. The kernel package initializes
Triton at import time, so its GPU import belongs to CVM validation, not the
CPU-only builder. These checks do not prove GPU boot or performance.
`scripts/render_fasth3_trial.py` renders a candidate from an existing release
without modifying the baseline. The intended trial version is `v0.6.0-rc.1`;
never reuse a published version, and keep v0.5.1 and its exact environment as the
rollback. No host-agent or host-daemon restart is part of this procedure.
The exact signed v0.5.1 runtime is retained in `runtime-baselines/v0.5.1.yml`;
tests pin its SHA-256 and verify trial releases against that baseline.

The performance target is a completed 15-second 1344×768 video in approximately
10 seconds, initially measured with text-only input. That initial benchmark scope
does not permit removing other modes. No four-H200 result establishes this target.

## Model and runtime coverage

FastVideo is the inference runtime; FastH3 is a family of distilled checkpoints.
Runtime support for a mode does not make a particular checkpoint support it.
The following source review uses FastVideo commit
`8db2d71ef217e84bec7dbb6e4ab65c92b144df4f`.

| Existing capability | Candidate route | Remaining work |
| --- | --- | --- |
| T2VA with generated audio | FastH3 four-step VSA DataFree | Quality, actual duration, Hopper memory and end-to-end latency validation |
| FL2VA with first frame or first and last frames | Base H3 FL2VA | Retain working semantics; no verified distilled FL2VA replacement identified in this review |
| Ref2VA character images | Base H3 Ref2VA initially; FastH3 OmniRef PDD is an acceleration candidate | Validate image ordering, subject labels, limits and visual fidelity |
| Ref2VA WAV/MP3 voice samples | Base H3 Ref2VA initially; OmniRef supports audio references | Preserve MP3 decoding, sample limits, speaker association and validation |
| Silent delivery and watermark setting | Serving adapter | Preserve controls independently of joint audio/video sampling |
| Seed, size, duration and sampling parameters | Per-mode request adapter | Preserve supported behavior and reject incompatible settings explicitly |

The four-step T2VA checkpoint is
`FastVideo/FastVideo-FastH3-4-step-Preview-v1-VSA-DataFree`. Its schedule uses five
sigma-grid points for four transformer evaluations. It must not be substituted
for base FL2VA or Ref2VA weights.

The upstream Ref2VA student is `FastVideo/FastVideo-FastH3-Omni-8-Step-V1`.
Its PDD recipe uses eight evaluations and its own export metadata, reference VSA
policy and base-model revision. Do not copy the T2VA schedule, sparsity tile size
or kernel assumptions into it. Source support is evidence of an integration path,
not validation of our API or of four-H200 operation.

Negative guidance is not an existing native H3 capability. Preserve the current
documented limitation; do not silently rewrite prompts. The gateway currently
accepts `i2va`, but the deployed backend rejects it: single-image generation uses
`fl2va`. Parity covers working behavior, not a new claim of I2VA support.

## Proposed migration constraints

- Keep the public model ID, multipart fields, authentication, billing and enabled
  moderation behavior. Preserve pre=`true`, post=`false`, audio=`false`, and do not
  bundle an unrelated Guard rollout.
- Keep MiniMax on GPUs 0–3, Qwen on 4–6 and Guard on 7. All video modes share one
  serialized generation capacity. Do not start independent H3 and FastH3 servers
  competing for GPUs 0–3.
- Retain base H3 as the functional fallback until each accelerated mode passes
  parity checks. A fallback needs an implemented placement/loading strategy; a
  second endpoint alone is insufficient.
- Establish the memory budget for the base FL2VA transformer, base Ref2VA
  transformer, distilled transformer(s), shared encoder/VAEs, activations and
  compile caches. Resident coexistence is unproven. Switching models may preserve
  functionality but adds latency; measure mode transitions as well as warm calls.
- Package every needed weight and configuration in measured, offline model packs.
  Preserve internal-only model networking and private ephemeral output/cache
  storage. Publish eventual images only to `ccs-images-public` GAR.
- Keep the released `tinfoil-config.yml` unchanged until a complete candidate has
  built, booted and passed the acceptance checks below.

## Acceptance checks before replacing the service

1. Run existing client forms for T2VA, first-frame FL2VA, first/last-frame FL2VA,
   multiple character references and character-plus-WAV/MP3 voice references.
   Check conditioning semantics and output quality, not only HTTP status.
2. Verify audio-on/off, watermark settings, seeds, supported canvases and actual
   encoded durations. The published 345-frame performance shape is 14.375 seconds
   at 24 FPS; it does not satisfy a full 15-second output requirement. Validate the
   pinned runtime's alignment and maximum rather than silently shortening output.
3. Exercise unsupported fields, malformed media, moderation failures, billing
   failures and cancellation. Health and aggregate metrics must still work, and
   request content must not appear in logs or leave the CVM.
4. Measure cold startup, warm rendering, mixed-mode switching, peak memory per
   GPU, output encoding and complete authenticated HTTP latency. A text-only
   warm result does not establish performance for reference-conditioned modes.

For a permanent migration, the next decision is the model-residency strategy.
The current evidence does not justify an all-mode FastH3 configuration or a
10-second performance promise. The temporary trial is authorized separately;
it does not authorize new cloud GPU resources or provisioning.

## Reviewed upstream sources

- [H3 support matrix](https://github.com/hao-ai-lab/FastVideo/blob/8db2d71ef217e84bec7dbb6e4ab65c92b144df4f/docs/inference/support_matrix.md)
- [FL2VA first/last-frame implementation example](https://github.com/hao-ai-lab/FastVideo/blob/8db2d71ef217e84bec7dbb6e4ab65c92b144df4f/examples/inference/basic/basic_minimax_h3_fl2va.py)
- [Distilled schedules and Ref2VA PDD contract](https://github.com/hao-ai-lab/FastVideo/blob/8db2d71ef217e84bec7dbb6e4ab65c92b144df4f/docs/inference/fasth3-distilled.md)
- [Four-Hopper T2VA profile](https://github.com/hao-ai-lab/FastVideo/blob/8db2d71ef217e84bec7dbb6e4ab65c92b144df4f/examples/inference/basic/basic_fasth3_h100.yaml)
