# Host benchmark and acceptance record

## Environment checked before model startup

- Host GPU: NVIDIA GeForce RTX 3060, 12 GB.
- Windows NVIDIA driver: 591.86; `nvidia-smi` reports CUDA 13.1.
- Docker Desktop: 29.9.2, Linux containers; Docker Compose 5.6.1.
- WSL2 default distribution: Ubuntu, WSL version 2.
- Docker GPU passthrough: passed with `nvidia/cuda:13.0.0-base-ubuntu24.04`; the container saw the RTX 3060 and driver 591.86.
- `ddu_inference_bus`: absent before this deployment; no running or stopped `ddu-inference` container was present.
- Existing Hugging Face cache: `C:\Users\marcu\.cache\huggingface\hub`, outside this repository. The Qwen3.5 checkpoint was not cached at preflight.
- vLLM image tag pulled: `vllm/vllm-openai:v0.32.0`; local image digest: `sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34`.
- Model revision: `7a613872f394578b0b52b683ff4ac47516b4bcaf`.

## Measurements

Host validation was run on 2026-10-28 UTC with Windows 11 Home build 26200, WSL2 Ubuntu, Docker Engine 29.9.2, Compose 5.6.1, NVIDIA GeForce RTX 3060 12 GB and driver 591.86. The pre-start device-wide memory sample was 1,366 MiB. The vLLM image was `vllm/vllm-openai:v0.32.0@sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34`; the model revision was `7a613872f394578b0b52b683ff4ac47516b4bcaf` (5.14 GiB). SHA-256 of `model.safetensors`: `5344d6c32686889991812e25b0ca363b6983c9b585ff44f5f4c5b2100354646a`; SHA-256 of `model_mtp.safetensors`: `7f993e20804b2176aacc2508eed509f1f2c7256e5ce4a0427c0442dfe461cbe1b`.

First cold-cache startup for 4096 context / 2 sequences took about 245 seconds from vLLM process start to API startup. The 8192 / 4 restart with model weights cached took about 149 seconds. Both profiles used 0.75 GPU memory utilization and passed health, model-alias and structured-output checks. Test batches used the two synthetic workloads, strict JSON schemas, `--disable-thinking`, 128 max output tokens, 20 requests per concurrency, one warmup per workload and no retries.

| Profile | Client concurrency | Success | Latency p50/p95 (s) | Server queue p50/p95 (ms) | TTFT p50/p95 (ms) | Output tokens/s | Peak device VRAM |
|---|---:|---:|---:|---:|---:|---:|---:|
| 4096 context / 2 sequences | 1 | 20/20 | 0.502 / 0.641 | 0.007 / 0.010 | 51.578 / 58.169 | 55.682 | 9,530 MiB |
| 4096 context / 2 sequences | 2 | 20/20 | 0.532 / 1.031 | 0.007 / 0.012 | 55.243 / 76.196 | 100.710 | 9,530 MiB |
| 4096 context / 2 sequences | 4 | 20/20 | 1.299 / 1.587 | 456.516 / 739.860 | 65.000 / 71.003 | 105.485 | 9,530 MiB |
| 8192 context / 4 sequences | 1 | 20/20 | 0.513 / 0.748 | 0.007 / 0.009 | 53.064 / 71.652 | 55.670 | 9,543 MiB |
| 8192 context / 4 sequences | 2 | 20/20 | 0.543 / 0.823 | 0.008 / 0.015 | 56.781 / 84.740 | 103.451 | 9,543 MiB |
| 8192 context / 4 sequences | 4 | 20/20 | 0.599 / 0.893 | 0.008 / 0.015 | 66.582 / 91.513 | 182.816 | 9,543 MiB |

The queue increase at concurrency 4 in the 4096 / 2 configuration is expected because vLLM serves at most two active sequences; the 8192 / 4 profile admits all four. Device-wide memory was sampled once per second with `nvidia-smi`; peaks include Windows display and other GPU processes. No OOM or failed request was observed. These are short synthetic probes on one host, not a capacity or gameplay quality claim.

Run `scripts/benchmark.py` from a trusted client attached to `ddu_inference_bus`; it writes only sanitized per-request metrics. The script records total latency, client dispatch delay, server queue time, time-to-first-token, output-token counts/rate, retry counts, and success rate. The table above is the sanitized RTX 3060 host result. Do not infer vLLM performance from the historical llama.cpp results in M1.5.

## Historical llama.cpp reference

The companion M1.5 validation used llama.cpp with Qwen3.5-4B Q4_K_M GGUF. It reached model health in about 20.48 seconds, passed two synthetic workload probes, sampled 4,307 MiB of device-wide GPU memory, and remained available after the history backend stopped. These are historical llama.cpp observations only; they are not vLLM measurements or performance targets.

## Lifecycle evidence (2026-10-28)

The companion backend was stopped while the vLLM service stayed healthy and both independent synthetic inference workloads succeeded. The vLLM service was then stopped with the backend running: `/healthz` and database-backed `/readyz` remained healthy, `/api/v1/status` reported degraded inference, and the M2 fake-turn/replay smoke passed. Restarting vLLM restored ready status without restarting the backend.

## Required host checks

- [x] `/health` returns HTTP 200 and `/v1/models` contains `ddu-narrator`.
- [x] Danish history and fictional examiner structured outputs both pass schema and `finish_reason == "stop"` checks.
- [x] Sequential and simultaneous client calls succeed with one resident model.
- [x] Concurrency 1, 2, and 4 is measured without OOM; queue wait, success rate, p50/p95, and output tokens/s are recorded.
- [x] Inference remains available when the backend stops; backend readiness and M2 fake turn remain available when inference stops.
- [x] Restart restores model readiness without restarting the backend.
- [x] Rollback to the original pinned llama.cpp image and verified GGUF succeeds, then vLLM recovers, with the backend database volume unchanged and no alias/host-port conflict.
- [x] Container inspection confirms no published inference port and the expected single `ddu-llm` alias owner.
