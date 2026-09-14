# Confidential Qwen3-Omni + MiniMax-H3 production runtime

This repository is the public, signed Tinfoil release provenance for the
production dual-model confidential-inference CVM: Qwen3-Omni-30B-A3B-Instruct
(chat/omni) and MiniMax-H3 FL2VA (video generation) co-located on one
eight-GPU H200 CVM.

## What is attested

`tinfoil-config.yml` is the exact measured runtime exported from the production
CVM specification (`cvmctl export-runtime -f vm-qwen-minimax-prod.yml`). It pins
both model references and MPKs, LiteLLM and vLLM-Omni image digests, resource
allocation, and all command lines. Three application containers share the
`models` network. A mandatory paid gateway starts before LiteLLM and admits
Qwen chat/audio and MiniMax multipart video requests through CCS before calling
the model servers. It validates output meters and persists checkpoints before
releasing output, with no optional paid flag, worker-hook installation or free
fallback. In the published v0.0.5 runtime, Qwen input-meter code is staged into a private
temporary import overlay; its site-packages and root filesystem remain read-only.

**v0.0.5 is published and immutable.** Its runtime SHA-256 is
`14cec7260731d54d538b75e2d06e5072029c4be3549f4c88dd962eccc9cf8111`.
This is the runtime digest, not the release attestation lookup digest. Publishing
changes the latest expected measurement used by routers and must be coordinated
with the Model CVM cutover. It does not activate the separately reviewed CCS
billing policy or enroll API keys.

The release workflow uses Tinfoil's pinned measurement action to publish a
Sigstore-signed deployment record and expected TDX measurements. A Tinfoil
verifier compares those expected measurements with a fresh quote from the live
CVM and verifies the attested TLS/HPKE key binding.

## Readable sources and OCI migration

The `images/` sources prepare the replacement for v0.0.5; adding them does **not**
change the published runtime or fix an already deployed v0.0.5 VM. Until both new
images are built and verified, `tinfoil-config.yml` remains the historical runtime.
Do not publish a new runtime from this preparation step.

- `images/paid-gateway/` contains the mandatory gateway, its baked LiteLLM config,
  entrypoint, and offline tests. Runtime code uses the pinned LiteLLM application's
  Python environment, not a second Python installation. Test dependencies stay
  in the Dockerfile's `test` stage, outside the final `runtime` image.
- `images/qwen-metered/` installs the hash-checked vLLM-Omni meter patch at **image
  build time**. Its final image needs no runtime source extraction, installer, or
  temporary Python overlay. The test stage checks the pristine pinned upstream
  source as well as the patched code.
- `scripts/source_delta.py` verifies v0.0.5's runtime hash, decodes its bundles as
  data, and shows readable source differences, including added/deleted modules.
  Review packaging and tests in the ordinary Git diff too. Fetch the immutable
  baseline with `git fetch origin tag v0.0.5` before running this script.

The gateway sources address all three findings from
[PR #2's review](https://github.com/aptos-labs/confidential-qwen-minimax-prod/pull/2#pullrequestreview-5194014250):
visible reasoning/refusal/tool/function output requires authoritative token IDs;
valid tool/function terminal reasons are accepted; and MiniMax admission uses
text plus actual uploaded images rather than always claiming image input.
Regression tests also cover malformed response containers, streaming audio shape,
and failure terminals, with only acknowledged checkpoints retained on late failure.
These are source fixes for a **new** image/runtime release, not a change to v0.0.5.

## Image publication and verification

1. Review and merge the readable source/image PR. Inspect **every review thread**,
   including neutral/comment-only bot reviews; green checks alone are insufficient.
   PR CI runs policy/script tests, both Dockerfile `test` targets, and final-runtime
   smoke checks with all capabilities dropped, read-only roots, and no network.
   It has no registry, attestation, or deployment privileges. Source directories
   must remain searchable without DAC capabilities; privileged build tests alone
   do not establish this.
2. Dispatch **Publish runtime images** on the reviewed `main` commit, passing its
   full 40-character hash as `source_sha`. Checkout, workflow SHA, and main ancestry
   must all agree. A branch or image tag is not proof of reviewed content.
3. Keep the hosted-runner storage gates: gateway 4 GiB, Qwen 24 GiB, and fresh
   anonymous verification 28 GiB. If a measured gate fails, stop and obtain
   approval for a larger runner; do not delete preinstalled tools or skip tests.
4. Record the workflow's actual build-output digests for these code-only packages:
   - `ghcr.io/aptos-labs/confidential-qwen-minimax-paid-gateway`
   - `ghcr.io/aptos-labs/confidential-qwen-minimax-qwen-metered`

   The workflow tests the final images read-only, without network or GPUs, and
   signs SLSA provenance for the exact digests. No weights or credentials belong
   in the image contexts. The `source-<sha>` tags are locators only.
5. New GHCR packages may be private. A package administrator must make **each**
   package Public in GitHub's package settings. There is no supported visibility
   update in the Packages REST API. Rerun the failed anonymous-verification job of
   the same run so it checks the same digests; do not accept an authenticated pull
   or copy a registry token onto the H200 host.
6. Require the fresh-runner anonymous digest pulls and both final-image smoke
   checks to pass. Independently verify each image's provenance, binding it to
   the reviewed source and publishing workflow, for example:

   ```bash
   gh attestation verify "oci://$IMAGE_REF" \
     --repo aptos-labs/confidential-qwen-minimax-prod \
     --signer-workflow aptos-labs/confidential-qwen-minimax-prod/.github/workflows/publish-runtime-images.yml \
     --source-digest "$SOURCE_SHA" --signer-digest "$SOURCE_SHA"
   ```

   `IMAGE_REF` must be one of the expected packages with its actual `@sha256:`
   digest, not a tag. Record the verified source, base digest, image digest, run,
   and smoke/anonymous-pull results together. Script/policy tests alone do not
   establish that an image builds or runs.
7. Only then replace the encoded runtime bundles with those verified digests in
   the production Model specification and exported runtime. Review that small
   runtime change separately, recheck all threads, and publish a **new** signed
   release. Preserve the v0.0.5 tag and assets. The Router does not need an image
   bump just because the Model runtime changes.

Image publication never dispatches a Tinfoil runtime release or deploys a VM.
Live TLS/attestation/authentication, both model paths, cancellation/durability,
and independently reconciled usage remain cutover gates. Infrastructure approval
and a separately reviewed billing-activation change are still required. Neither
image publication nor a runtime release activates billing or enrolls accounts/keys.

## Runtime release process

1. Update the production `cvmctl` spec with the verified image digests.
2. Export its exact measured runtime:

   ```bash
   cvmctl export-runtime -f vm-qwen-minimax-prod.yml > tinfoil-config.yml
   ```

3. Review and commit the generated `tinfoil-config.yml`.
4. Run the **Tinfoil Release** workflow with a new, unused immutable tag.
   Never reuse a published tag or release.
5. Deploy that exact production specification, setting its metadata repository
   and tag to this repository and release.

Any runtime change—including a model, MPK, image digest, mux rule, or vLLM
arguments—requires a new release before deployment.

## Security boundaries

This repository intentionally contains no credentials. The certificate
authorization token and native `USAGE_REPORTER_SECRET` remain only in the protected
external configuration. The gateway's CCS destination and reporter identity are
fixed in measured code. Missing or invalid reporter credentials fail startup;
unmapped keys and unavailable admission fail closed. Both production models
require explicit active account/key mappings and validated usage settlement.
