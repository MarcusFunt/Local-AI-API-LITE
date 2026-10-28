# Deployment

## Host requirements

- Windows 11 with WSL2 and Docker Desktop using Linux containers.
- NVIDIA driver and WSL GPU passthrough visible from a Docker container.
- Docker Compose v2 and enough free disk for the pinned vLLM image and the model cache.
- A trusted client on the external Docker network `ddu_inference_bus`.

Run `bash scripts/check_prereqs.sh` from a WSL distribution with Docker Desktop integration enabled. The check is read-only: it reports GPU visibility, Docker/Compose, disk space, and whether the shared network or legacy inference container exists. Create the network only when needed:

```sh
docker network inspect ddu_inference_bus >/dev/null 2>&1 || docker network create ddu_inference_bus
```

## Configuration and startup

1. Copy `.env.example` to `.env`. Set `HF_CACHE_DIR` to the same persistent Hugging Face cache used by other local model tooling, if appropriate. Keep it outside the repository. Use a forward-slash absolute path such as `C:/Users/name/.cache/huggingface` in PowerShell Compose or `/mnt/c/Users/name/.cache/huggingface` from WSL.
2. Review `MODEL_ID`, its immutable `MODEL_REVISION`, the API alias, context, sequence limit, GPU memory fraction, and reasoning parser. Do not change the image tag/digest without repeating image and model validation.
3. Run `docker compose config --quiet`, then `docker compose up -d`. The server binds `0.0.0.0:8080` inside the container only. There is no Compose `ports` mapping.
4. Follow startup with `docker compose logs -f vllm`; the first boot downloads the pinned public model into the persistent cache. Subsequent restarts reuse it.
5. From an attached trusted client, check `/health`, `/v1/models`, and both structured-output workloads as shown in the root README.

The deployment is one Compose project (`local-ai-api-lite`) and one vLLM service. A separate history backend, database, or client container is not included here.

## Stop and restart

```sh
docker compose stop
docker compose start
docker compose restart vllm
docker compose down
```

`docker compose down` removes only this project's container and project network references; the Hugging Face cache remains on the host. Do not use `down -v` for routine shutdown. Do not stop the history database/backend project as part of inference maintenance.

## Network and rollback safety

The API is unauthenticated and intended only for trusted containers on `ddu_inference_bus`. Never publish port 8080 on the host or Internet. Before switching an existing inference deployment, inspect running containers and network aliases, confirm the database/backend project remains up, and ensure only one container owns `ddu-llm` at a time. Follow [CUTOVER_AND_ROLLBACK.md](CUTOVER_AND_ROLLBACK.md); do not remove or recreate database volumes.
