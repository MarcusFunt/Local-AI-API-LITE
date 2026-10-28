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

| Profile | Startup | Concurrency | Success | p50/p95 latency | Tokens/s | Peak VRAM | Result |
|---|---:|---:|---:|---:|---:|---:|---|
| 4096 context, 2 sequences | Pending | Pending | Pending | Pending | Pending | Pending | Not yet run |
| 8192 context, 4 sequences | Pending | Pending | Pending | Pending | Pending | Pending | Not yet run |

Run `scripts/benchmark.py` from a trusted client attached to `ddu_inference_bus`; it writes only sanitized per-request metrics. Record actual host output here after the vLLM model is loaded. Do not infer vLLM performance from the historical llama.cpp results in M1.5.

## Required host checks

- [ ] `/health` returns HTTP 200 and `/v1/models` contains `ddu-narrator`.
- [ ] Danish history and fictional examiner structured outputs both pass schema and `finish_reason == "stop"` checks.
- [ ] Sequential and simultaneous client calls succeed with one resident model.
- [ ] Concurrency 1, 2, and 4 is measured without OOM; queue wait, success rate, p50/p95, and output tokens/s are recorded.
- [ ] Inference remains available when the backend stops; backend readiness and M2 fake turn remain available when inference stops.
- [ ] Restart restores model readiness without restarting the backend.
- [ ] Container inspection confirms no published inference port and the expected single `ddu-llm` alias owner.
