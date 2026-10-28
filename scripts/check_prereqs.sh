#!/usr/bin/env bash
set -euo pipefail

failures=0

if ! command -v nvidia-smi >/dev/null 2>&1; then
  printf '%s\n' 'FAIL: nvidia-smi is not available in this WSL distribution.'
  failures=$((failures + 1))
else
  printf '%s\n' 'GPU:'
  nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
fi

if ! command -v docker >/dev/null 2>&1; then
  printf '%s\n' 'FAIL: Docker CLI is unavailable; enable Docker Desktop WSL integration.'
  failures=$((failures + 1))
else
  docker version --format 'Docker client {{.Client.Version}} / server {{.Server.Version}}'
  docker compose version
  docker info --format 'Docker OS={{.OSType}} arch={{.Architecture}}'
fi

printf '%s\n' 'Disk space:'
df -h .

if docker network inspect ddu_inference_bus >/dev/null 2>&1; then
  printf '%s\n' 'Network ddu_inference_bus exists.'
else
  printf '%s\n' 'Network ddu_inference_bus is missing; create it before compose up.'
fi

legacy=$(docker ps -a --filter name=ddu-inference --format '{{.Names}} {{.Status}}' 2>/dev/null || true)
if [[ -n "$legacy" ]]; then
  printf 'Legacy inference containers (inspect before any cutover):\n%s\n' "$legacy"
else
  printf '%s\n' 'No ddu-inference container exists.'
fi

if (( failures > 0 )); then
  exit 1
fi
