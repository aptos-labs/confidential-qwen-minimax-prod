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
fallback. Qwen input-meter code is staged into a private temporary import overlay;
the pinned image's site-packages and root filesystem remain read-only.

The v0.0.5 candidate has runtime SHA-256
`14cec7260731d54d538b75e2d06e5072029c4be3549f4c88dd962eccc9cf8111`.
This is the runtime digest, not the release attestation lookup digest. Publishing
changes the latest expected measurement used by routers and must be coordinated
with the Model CVM cutover. It does not activate the separately reviewed CCS
billing policy or enroll API keys.

The release workflow uses Tinfoil's pinned measurement action to publish a
Sigstore-signed deployment record and expected TDX measurements. A Tinfoil
verifier compares those expected measurements with a fresh quote from the live
CVM and verifies the attested TLS/HPKE key binding.

## Release process

1. Update the production `cvmctl` spec.
2. Export its exact measured runtime:

   ```bash
   cvmctl export-runtime -f vm-qwen-minimax-prod.yml > tinfoil-config.yml
   ```

3. Review and commit the generated `tinfoil-config.yml`.
4. Run the **Tinfoil Release** workflow with a new immutable tag, for example
   `v0.0.1`.
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
