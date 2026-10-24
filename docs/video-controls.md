# MiniMax reference images, voice samples and silent output

The v0.4.0 measured runtime contains Ref2VA and silent-output controls, with verified
image/model provenance recorded in the README. Native negative guidance is unsupported
by the released H3 checkpoints. Prompt rewriting is not enabled.

## Character references

The public model ID remains `minimax-h3-fl2va` for existing clients. Select reference mode
with `extra_params={"task":"ref2va","duration":5.0,"audio_flow_shift":3.0}`. Upload 1–9
PNG/JPEG files using repeated `input_references` multipart parts. The aggregate body limit
remains 64 MiB. Every image is moderated when input moderation is enabled. A reference-only
request without an image is rejected. FL2VA retains its limit of two images; repeated singular
`input_reference` fields and mixing singular/plural fields remain invalid.

Images are numbered in upload order. Refer to them in the prompt as `<Picture 1>`,
`<Picture 2>`, etc. For example:

```text
<Picture 1> contains four views of Alice. <Picture 2> contains four views of Bob.
Preserve their respective faces, hairstyles and outfits.
Open on a wide shot of Alice and Bob entering a railway station.
```

Reference images guide appearance; they do not bind the shot's first/last frame. Association
between a character and images is prompt-based, with no structured character-ID field.
Three composite character cards count as three images. Twelve separate views exceed the
nine-image limit. Character fidelity requires evaluation on representative inputs.

## Voice samples

The v0.5.0 measured runtime includes WAV/MP3 voice-sample support in its verified gateway
and worker images. Live readiness and voice quality remain rollout checks.
The existing H3 image and combined checkpoint already support reference audio. No model
repack or GPU-placement change is required.

For `task=ref2va`, add WAV or MP3 uploads to the same repeated `input_references` field:

```bash
curl --fail-with-body --max-time 1800 \
  https://host0.inference.aptoslabs.com/v1/videos/sync \
  -H "Authorization: Bearer $CCS_API_KEY" \
  -F 'model=minimax-h3-fl2va' \
  --form-string 'prompt=<Subject 1> is the character in <Picture 1>. <Audio 1> is the voice-timbre reference for <Subject 1> (S1). <Subject 1> (S1) smiles and says <d>[English] Welcome back. It is good to see you.</d>' \
  -F 'input_references=@character.png;type=image/png' \
  -F 'input_references=@voice.wav;type=audio/wav' \
  -F 'width=1344' -F 'height=768' -F 'aspect_ratio=16:9' -F 'fps=24' \
  -F 'num_inference_steps=50' -F 'flow_shift=12' -F 'seed=1101' \
  -F 'generate_sound=true' \
  -F 'extra_params={"task":"ref2va","duration":5.0,"audio_flow_shift":3.0}' \
  -o voice-reference.mp4
```

WAV samples must use a `.wav` filename and MIME type `audio/wav`, uncompressed signed
16-bit PCM, one or two channels, and a sample rate between 16 and 48 kHz. MP3 samples use a
`.mp3` filename and MIME type `audio/mpeg`, for example:

```bash
-F 'input_references=@voice.mp3;type=audio/mpeg'
```

The gateway checks WAV RIFF/chunk integrity and every PCM frame. MP3 uploads always receive
a full, bounded FFmpeg decode in the private worker before generation, even when all content
classifiers are disabled. This CPU validation needs no model readiness. Invalid recordings
return `400 invalid_request`; decoder unavailability/timeouts return `503 moderation_unavailable`.
The decoded duration, including all WAV and MP3 samples in the request, must satisfy these limits:
2–15 seconds per sample, at most three samples, 15 seconds of audio in total, and 3 MiB per
sample. Up to nine images plus three audio files fit the existing 64 MiB request limit.
At least one image is required. Audio uploads are rejected for T2VA and FL2VA.
URLs, base64 fields, video uploads and ElevenLabs voice IDs are not accepted. To convert
an exported recording, use `ffmpeg -i voice.mp3 -ac 1 -ar 24000 -c:a pcm_s16le voice.wav`;
select a 2–15 second excerpt when necessary.

`<Picture N>` and `<Audio N>` are numbered independently in their respective upload order.
Bind each audio label to a subject and speaker ID in the prompt to associate multiple voices.
The original multipart bytes reach H3 unchanged. The deployed upstream pipeline crops each
reference audio to the output video duration before encoding: a five-second render uses only
the first five seconds of a longer sample. Voice similarity, exact dialogue timing and lip-sync
quality require evaluation; this interface does not guarantee an identical voice or lossless
reuse of a supplied dialogue recording.

Input-audio content moderation is independent of prompt/image and output moderation.
`CCS_MODERATION_AUDIO_ENABLED=false` is the default for trusted samples. Setting it to
`true` classifies every voice sample with the existing internal Qwen Omni, normalizing it to
24 kHz stereo PCM in request-private scratch. Enabled checks fail closed. The setting is
operator-owned and cannot be overridden by request fields or headers. Use the renderer's
`--voice-samples` flag to expose it in the gateway environment. See [moderation configuration](moderation.md#stage-configuration).

## Silent delivery

`generate_sound=false` removes audio from the returned MP4. Omitted/true retains the existing
audio behavior. The patch checks explicit field presence because the upstream request schema
defaults the omitted field to false. Tests use that actual pinned Pydantic schema, including
its defaults, instead of a synthetic request default.
The exact-source patch in `images/minimax-controls` discards the audio output
before the shared sync/async MP4 assembly path. Video frames, sampling and seed are unchanged.
H3 still computes its joint audio/video latents; this setting does not avoid that GPU work.

The model image derives from the exact deployed `minimax-watermark` digest. Installation
checks source hashes before applying the silent-output and watermark-control patches. CPU tests execute
the patched serving method with synthetic generation, then encode and decode actual MP4s to
verify stream presence/absence and both watermark settings. Final-image checks run with no GPU,
no network, a read-only root filesystem and no Linux capabilities. Live GPU rendering is a
separate release check.

## Visible watermark

The model container accepts the operator-owned startup setting
`CCS_MINIMAX_WATERMARK_ENABLED`. Omitted or literal lowercase `true` retains the original
`NOT FOR COMMERCIAL USE` overlay. Literal lowercase `false` skips the overlay before
encoding, preserving generated pixels and audio behavior. Other values fail at module startup.
The setting is captured once when the media module loads and applies to every encoding path;
it is not a request parameter. It does not remove a watermark from an existing video.

Use the runtime renderer's `--watermark-control` flag to expose the setting only to the
MiniMax container, then set its value in the protected fleet `spec.external.env` map.
The first rollout requires a rebuilt controls image and signed runtime. Once exposed,
changing the value requires a model-container restart. Model weights, GPU placement,
generation seed, and moderation configuration are unaffected. Release v0.5.0 predates this
control and always applies the overlay.

## Negative prompts

The generic API accepts `negative_prompt`, but the deployed H3 pipeline does not consume it.
Its model constructor explicitly rejects CFG parallelism because H3 is CFG-distilled and has
no negative branch. The [Diffusers H3 encoder](https://github.com/huggingface/diffusers/blob/main/src/diffusers/modular_pipelines/minimax_h3/encoders.py)
independently documents the same limitation. A prompt-based exclusion adapter would be a
separate behavior requiring an explicit product decision; it is not native negative guidance.

## Combined checkpoint and runtime

The combined model volume retains the existing FL2VA files and adds `Ref2VA/model_index.json`
and `Ref2VA/transformer/*` from pinned `MiniMaxAI/MiniMax-H3` revision
`42ed227ee7df40d41602854ae760620d6eb651fe`. Shared encoders/VAEs load from FL2VA. Each input
file must match its Hugging Face Git blob or LFS SHA-256 before packing with the pinned
Modelwrap toolchain. The new pack replaces the existing MiniMax volume: the signed five-disk
hardware profile, Qwen volume and eight physical GPUs remain.

The renderer accepts `--minimax-image`, `--minimax-pack` and `--minimax-repo` together, only
with exact image digests and model source revisions. The MiniMax command addresses the pack
root to load both DiTs. It uses DiT TP4, text-encoder TP4, USP1 and the existing VAE tiling on
GPUs 0–3. Pure USP4 would replicate both ~61.7 GiB DiTs on every GPU; tensor parallelism
shares their weights across the existing four H200s. GPUs 4–6 remain Qwen and GPU 7 Guard.
Live boot, peak memory and latency must be verified for the new placement before reporting it
as ready. The endpoint still permits one active video request and rejects overload with 429.

## Build and release

Publish the gateway and worker for the shared nine-image parser, plus the model image through
`cloudbuild-minimax-controls.yaml`. All use `ccs-images-public`. Supply the reviewed source
archive and exact `_SOURCE_SHA`; verify build provenance and anonymous pulls. Generate a new
signed runtime only after all three image digests and the combined model pack are verified.
Keep pre-moderation enabled and post-moderation disabled for the requested deployment.
Replace only `qwen-minimax` on `host0`; do not restart the host agent or daemon.
