# Engine and model decision

## V1 choice

V1 uses the official `vllm/vllm-openai` CUDA image at release `v0.32.0`, pinned to digest `sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34`. The vLLM 0.32.0 release notes list Qwen3.5 support and publish this image tag: [vLLM v0.32.0 release](https://github.com/vllm-project/vllm/releases/tag/v0.32.0). The official [vLLM Docker deployment guide](https://docs.vllm.ai/en/latest/deployment/docker/) documents the OpenAI-compatible server image.

The first checkpoint candidate is `RedHatAI/Qwen3.5-4B-quantized.w4a16`, pinned to Hub revision `7a613872f394578b0b52b683ff4ac47516b4bcaf`. The [model card](https://huggingface.co/RedHatAI/Qwen3.5-4B-quantized.w4a16) identifies Apache-2.0 licensing, W4A16 weights, and a published 5.2 GB checkpoint size. Runtime VRAM usage is higher than the file size. vLLM's [quantization documentation](https://docs.vllm.ai/en/stable/features/quantization/) lists Ampere support for the relevant 4-bit serving implementations.

The initial launch uses `--language-model-only`, 4096 context, two sequences, 0.75 GPU memory utilization, and the Qwen3 reasoning parser. These are candidate runtime settings; successful CLI parsing does not prove model correctness or fit. The actual RTX 3060 load and schema outputs are recorded in [BENCHMARKS.md](BENCHMARKS.md).

## Alternatives and limits

- A compatible Qwen3.5 GPTQ/AWQ INT4 checkpoint is the first fallback if this W4A16 checkpoint fails on the pinned vLLM release or the RTX 3060.
- SGLang is an opt-in measured alternative only after vLLM passes the contract and host gates. It must use the same underlying model and cannot claim `ddu-llm` while vLLM is active.
- llama.cpp/GGUF remains a rollback reference. The companion repository's M1.5 timings describe llama.cpp and must not be reported as vLLM results.
- One RTX 3060 benchmark does not establish classroom capacity or medical correctness.
