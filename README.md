# Confidential Qwen3-Omni + MiniMax-H3 production runtime

This repository is the public, signed Tinfoil release provenance for the
production dual-model confidential-inference CVM: Qwen3-Omni-30B-A3B-Instruct
(chat/omni) and MiniMax-H3 FL2VA (video generation) co-located on one
eight-GPU H200 CVM.

## What is attested

`tinfoil-config.yml` is the exact measured runtime exported from the production
CVM specification (`cvmctl export-runtime -f vm-qwen-minimax-prod.yml`). It pins
both model references and MPKs, the vLLM-Omni image digests, the nginx path-mux,
resource allocation, and the complete container command lines.

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
authorization token remains only in the protected host-side CVM specification.
