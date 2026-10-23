# MiniMax reference images and silent output

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

## Silent delivery

`generate_sound=false` removes audio from the returned MP4. Omitted/true retains the existing
audio behavior. The patch checks explicit field presence because the upstream request schema
defaults the omitted field to false. Tests use that actual pinned Pydantic schema, including
its defaults, instead of a synthetic request default.
The exact-source patch in `images/minimax-controls` discards the audio output
before the shared sync/async MP4 assembly path. Video frames, sampling and seed are unchanged.
H3 still computes its joint audio/video latents; this setting does not avoid that GPU work.

The model image derives from the exact deployed `minimax-watermark` digest. Installation
checks the source hashes and preserves both watermark implementation files. CPU tests execute
the patched serving method with synthetic generation, then encode and decode actual MP4s to
verify stream presence/absence and watermark retention. Final-image checks run with no GPU,
no network, a read-only root filesystem and no Linux capabilities. Live GPU rendering is a
separate release check.

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
