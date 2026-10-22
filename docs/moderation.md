# MiniMax moderation

Status: v0.3.4 Triton cache fix prepared for live validation. `tinfoil-config.yml` pins the published,
verified worker and gateway images with the 4/3/1 allocation. Real-model quality and latency
remain unvalidated until the GPU checks below pass. The immutable unmoderated v0.2.1 baseline
is retained in `runtime-baselines/v0.2.1.yml`; the renderer always starts from that baseline.

The v0.3.0 live boot reached MiniMax readiness but Qwen failed startup. The exact Qwen image
contains vLLM/vLLM-Omni 0.28.0, whose CLI has no `--disable-log-requests` option and whose
`enable_log_requests` default is false. v0.3.1 removes that unsupported option and retains
stdout/stderr suppression. Images, model packs, 4/3/1 placement and all moderation checks
are unchanged. The corrected runtime still needs live readiness and generation checks.

The v0.3.1 live boot reached Qwen and MiniMax readiness, but Guard failed its startup
self-test. Exact-image CPU diagnosis found Transformers 5.14.1 returns `BatchEncoding`
from `apply_chat_template`, while the adapter assumed a tensor. The corrected adapter
explicitly requests a dictionary, counts `input_ids`, and forwards the attention mask.
The packaged-model smoke now exercises this production adapter on a synthetic prompt.
The worker boot check uses liveness; moderation readiness continues to gate every video.

The v0.3.2 live boot reached Qwen and MiniMax readiness, but moderation remained
unavailable. v0.3.3 adds fixed startup-stage diagnostics to the worker and aggregate health
to identify the remaining failure. It does not change policy, deadlines, or GPU placement.
No moderated video generation or post-check latency measurement has succeeded yet.

The v0.3.3 GPU traceback identified a Triton shared-library load from the `noexec` `/root`
tmpfs. PyTorch 2.13.0+cu130 uses Triton even for the eager Qwen3 rotary-embedding float
matrix multiplication; CPU-only smoke does not exercise this path. v0.3.4 sets
`TRITON_CACHE_DIR=/var/cache/moderation/triton` on a separate 256 MiB executable tmpfs
(`rw,size=256m,mode=0700,exec,nosuid,nodev`). The cache is private and ephemeral. `/root`
and `/tmp` retain `noexec`, and the container root remains read-only. The exact published
worker passed its real Guard self-test on an H200 in the existing CVM with these
restrictions and no network. Images, placement, and mandatory pre/post checks are unchanged.
This GPU startup result does not establish end-to-end generation latency or policy quality.

## Request flow and decisions

The gateway unconditionally checks every MiniMax `t2va`, `fl2va`, and `i2va` request:
admission → pre-check → generation → MP4 buffer → post-check → output checkpoint/settlement
→ response. The admission-404 billing fallback does not skip either moderation check.
There is no runtime moderation-disable flag. The worker is required for all video requests.

The pre-check runs Qwen3Guard-Gen-0.6B on the complete prompt using its native chat template.
Every Unsafe or Controversial severity blocks. Sexual labels block regardless of severity.
Only valid Safe/None may continue. A secondary Qwen3-Omni policy classification checks the
prompt and role-labeled negative prompt, followed by every uploaded image. No secondary
check can override a Guard block. Text is capped at 16 KiB per field and Guard at 8192 input
tokens; no input is silently truncated. Images must fully decode as static PNG/JPEG and fit
the pixel/byte limits. The gateway and worker share the same multipart parser.

The post-check validates the MP4, fully decodes its video/audio, and classifies each sampled
frame plus the complete audio track. At the validated constant 24 fps, frames 0, 24, 48, …
sample seconds 0, 1, 2, …; partial final seconds count, using the video duration rather than
AAC padding. Supported video duration is 4–15 seconds. Missing expected audio blocks;
`generate_sound=false` permits absent audio, but any present audio is still checked. Stereo
audio is resampled to 24 kHz PCM for Qwen. Frame checks are sequential to bound Qwen load.
The MP4 is returned unchanged only after every required check allows.

The internal worker response contains exactly `decision` and `category`. Successful
moderation returns HTTP 200, including a policy block. Worker errors return HTTP 503 with
`{"decision":"block","category":"moderation_unavailable"}`. The gateway converts policy
blocks into HTTP 400 `content_policy_violation` with a category and request ID; operational
errors into HTTP 503 `moderation_unavailable`. Neither path emits video headers or bytes.
Errors must never count as abuse strikes. CCS block persistence/tenant enforcement remains
separate W4 work. Blocked outputs are not checkpointed as successful video deliveries;
commercial charging policy is still unresolved.

## Policy coverage

Policy version: `minimax-h3-2026-10-21-v1`.
Source: [MiniMax H3 license, Exhibit A](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/42ed227ee7df40d41602854ae760620d6eb651fe/LICENSE),
revised 2026-08-02. The exact license snapshot and SHA-256 are recorded alongside this doc.
The Qwen policy rubric is measured code in `moderation_worker/policy.py`.

| Category | Detection / enforcement |
|---|---|
| A1 territory | External authorization/territory control; content cannot establish location |
| A2 law/IP | Qwen checks expressed illegal or infringing use; cannot verify all rights |
| A3/A4 harm | Qwen checks harmful content, intent and repurposing |
| A5 safeguard bypass | Guard and Qwen examine jailbreak attempts |
| A6 minors | Qwen checks exploitation/harm; no reliable age/identity certification |
| A7 false information/elections | Qwen checks expressed harmful misinformation intent |
| A8 fake engagement | Qwen checks fake reviews/engagement requests |
| A9 harassment | Qwen checks defamation/disparagement/harassment |
| A10 malware | Qwen checks intent to damage electronic systems |
| A11 PII | Qwen checks harmful disclosure requests |
| A12 disclosure | External marking/terms obligation, not content classification |
| A13 impersonation | Qwen checks expressed nonconsensual impersonation; verified consent is external |
| A14 high-risk decisions | Qwen checks expressed critical-domain automated decision uses |
| A15 regional norms | Qwen checks expressed violations; cannot infer applicable locale/norms |
| A16 extremism | Qwen checks violent extremism/terrorism |
| A17 discrimination | Qwen checks discriminatory harm |
| A18 vulnerable populations | Qwen checks exploitation/manipulation intended to harm |
| A19 military | Qwen checks military-purpose requests |
| A20 professional practice | Qwen checks expressed unlicensed practice; cannot verify licensing |
| sexual_content | Blanket product restriction, including adults; not an allegation of child exploitation |
| guard_unsafe / guard_controversial | Conservative severity blocks; do not invent a precise Exhibit A allegation from a broad Guard label |
| none | All required checks allowed |
| moderation_unavailable | Operational failure, never a user violation |

Exhibit A categories are encoded `aup_02` through `aup_20` (excluding A12). Qwen selects
sexual content first, then the lowest matching AUP number. Strict parsing rejects unknown
categories, contradictory allow/category combinations, duplicate keys, refusals, tool calls,
and truncated completions. Model input is untrusted data, not instruction authority.

## Runtime and privacy

| Physical GPUs | Assignment |
|---|---|
| 0–3 | MiniMax |
| 4 | Qwen Thinker |
| 5 | Qwen Talker, one replica |
| 6 | Qwen Code2Wav |
| 7 | Qwen3Guard in the moderation worker |

The worker embeds Guard revision `fada3b2f655b89601929198343c94cd2f64d93cc` in its image,
with per-file hashes, offline loading and no remote code. This uses the existing five-disk
hardware profile: no extra model volume and no repack of the 144 GB MiniMax model. The image
digest attests the Guard weights. Guard requires exactly one visible CUDA GPU; no silent
CPU fallback. The current second Qwen Talker replica is removed for the new layout.

The worker only joins `models`, never `gateway-egress`, and has no credentials or public
route. Calls go directly to the fixed internal Qwen endpoint. No user URLs are fetched.
Media scratch is request-private tmpfs with a 2 GiB cap. Decoders have CPU, address-space,
file-size, thread and wall-time limits; stderr is discarded. Linux enforces the address-space
limit; macOS local tests retain the other limits because Darwin rejects RLIMIT_AS.
Disconnects cancel in-flight checks and decoders. A cancelled/failed Guard execution is
quarantined until worker restart, preventing late CUDA work from overlapping another request.

Worker application logs/core dumps are disabled. The rendered runtime redirects model
backend stdout/stderr to `/dev/null`, because exception paths can print prompts even with
request logging disabled. This reduces backend debugging visibility; aggregate health and
metrics remain available. Neither model output explanations nor transcripts are exported.

Startup exposes liveness independently of readiness. The worker Docker boot healthcheck
uses liveness, so unavailable moderation does not prevent the shim from serving Qwen
or health diagnostics. MiniMax pre/post checks still fail closed until moderation is ready.
Readiness requires Guard's safe-text self-test and Qwen text/image/audio self-tests. Qwen
startup is retried; unavailable dependencies never enable generation without moderation.
The gateway's aggregate `/health` includes worker readiness and returns 503 if it is unavailable;
the gateway's boot liveness remains independent of model/worker readiness.
The private `/health/startup` endpoint and gateway aggregate health expose only an
allowlisted startup stage and a failure boolean. Stages distinguish CUDA discovery,
weight loading, Guard tokenization/generation/verdict parsing, and Omni text/image/audio
self-tests. No exception messages, model verdict text, prompts, or media enter these
diagnostics. The existing readiness and pre/post verdict contracts are unchanged.
One worker request runs at a time; overload returns a fixed operational block. Default
pre/post deadlines are 110/230 seconds in the worker and 120/240 in the gateway, bounded
by the admission deadline. These are provisional limits to tune in W6.

## Validation and delivery

Local checks (Python 3.12+, FFmpeg and ffprobe required):

```sh
python -m pytest -q
mypy images/paid-gateway/paid_gateway/moderation_contract.py images/paid-gateway/paid_gateway/moderation.py images/moderation-worker/moderation_worker scripts/render_moderated_runtime.py scripts/evaluate_moderation.py
docker buildx build --platform linux/amd64 --target test -f images/moderation-worker/Dockerfile .
```

CI checks policy parsing, byte-level gateway behavior, real synthetic H.264/AAC decoding,
frame/audio coverage, failures, cancellation and cleanup, the rendered placement/network
policy, and offline image smoke. Tests do not read user credentials/configuration. CPU-only
tests do not establish the accuracy or latency of either real moderation model. The image
smoke also runs the packaged Guard tokenizer and generation adapter on a synthetic benign
prompt using CPU-loaded weights. This catches dependency API incompatibility; production
startup still requires one CUDA GPU and never falls back to CPU.

After source review, publish the worker with `cloudbuild-moderation.yaml` and the gateway
with `cloudbuild-gateway.yaml`, submitting an archive of the reviewed commit and `_SOURCE_SHA`.
Both publish to `us-docker.pkg.dev/aptos-registry/ccs-images-public`; require successful tests,
anonymous exact-digest pulls, smoke and provenance before using either digest. Render:

```sh
python scripts/render_moderated_runtime.py --gateway "$GATEWAY_DIGEST" --worker "$WORKER_DIGEST" --output /tmp/moderated-runtime.yml
```

Review the rendered runtime and verify the fleet export is identical before cutting a new
signed release. Do not edit an existing signed release or substitute placeholder digests.
No script here deploys or restarts the host, VM or containers.

For v0.3.0, both images were built from source commit
`70b8e27d0f198cc9307e551f539b8565bdf39001`. Cloud Build tests, anonymous pulls and final-image
smoke passed. Google-signed provenance matches the source archive and output digests.
Provenance occurrences live in the build project `aptos-cloudbuild`, not the registry project
`aptos-registry`; query Container Analysis in the build project when verifying these images.
See the README for the build IDs and image references. Deployment for initial measurement
does not establish that the W6 quality/load requirements have passed.

W6 is required before rollout: benchmark 4/10/15-second clips with 0/1/2 input images under
normal Qwen text/audio traffic. Compare the 4/3/1 configuration against today's Qwen baseline;
record p50/p95/p99 latency, throughput, RAM/VRAM, queues, errors and cold boot behavior.
Build a labeled corpus for every content-checkable category and benign controls, including
multilingual/obfuscated prompts, image/audio injection, lyrics and non-speech audio.
`scripts/evaluate_moderation.py` runs inside the CVM against the private worker and emits
aggregate counts and latency, never examples. Its JSON manifest is a list of objects with
`stage` (`pre`/`post`), relative `file` (multipart body/MP4), `content_type`, `audio_expected`,
and `expected: {decision, category}`. Miss/false-block thresholds must be supplied explicitly.
Record sample counts and statistical uncertainty separately; an observed zero miss count
does not establish zero risk. Rerun the labeled evaluation quarterly.

One frame per second can miss short events between samples. Include these in the evaluation;
increase sampling or add temporal checks if required to meet the agreed threshold. Omni is
a general multimodal model, not a validated specialized safety classifier. No production
quality claim is made until real-model W6 results pass. Rollback must disable MiniMax or
restore a verified moderation release, rather than reopen an unmoderated endpoint.
