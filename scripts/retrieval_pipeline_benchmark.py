#!/usr/bin/env python3
"""Bounded synthetic benchmark for the private narrator, embed, and rerank services.

Run inside a trusted container attached to ddu_inference_bus. This runner never starts,
stops, restarts, or reconfigures model services, and it never writes prompt or output text.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "scripts" / "fixtures" / "retrieval_pipeline_v1.json"
EMBED_URL = "http://ddu-embed:8080"
RERANK_URL = "http://ddu-rerank:8080"
NARRATOR_URL = "http://ddu-llm:8080"
EXPECTED = {
    "embed": ("ddu-embed", "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3"),
    "rerank": ("ddu-rerank", "8215cf04918ba6f7b6a62bb44238ce2953d8831c"),
    "narrator": ("ddu-narrator", "7a613872f394578b0b52b683ff4ac47516b4bcaf"),
}
IMAGE_REFS = {
    "narrator": "vllm/vllm-openai:v0.32.0@sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34",
    "retrieval": "local-ai-api-lite-retrieval:0.1.0 (local image tag; inspect its local image ID separately)",
}
SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer", "evidence_ids"],
    "additionalProperties": False,
}


class BenchmarkError(RuntimeError):
    def __init__(self, category: str):
        super().__init__(category)
        self.category = category


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_request(method: str, url: str, payload: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=max(0.05, timeout)) as response:
            parsed = json.loads(response.read(2 * 1024 * 1024))
    except urllib.error.HTTPError as exc:
        raise BenchmarkError(f"upstream_http_{exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise BenchmarkError("upstream_timeout_or_unavailable") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BenchmarkError("invalid_json_response") from None
    if not isinstance(parsed, dict):
        raise BenchmarkError("invalid_response_shape")
    return parsed


def _http_get(url: str, timeout: float) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=max(0.05, timeout)) as response:
            body = response.read(32768)
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read(32768)
        status = exc.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, {}
    try:
        parsed = json.loads(body) if body else {}
        return status, parsed if isinstance(parsed, dict) else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status, {}


def _model_records(url: str, timeout: float) -> list[dict[str, Any]]:
    response = _json_request("GET", f"{url}/v1/models", None, timeout)
    records = response.get("data")
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise BenchmarkError("model_identity_unavailable")
    return records


def verify_service(
    name: str, url: str, timeout: float, readiness_timeout: float = 0,
) -> dict[str, Any]:
    ready_endpoint = "/health" if name == "narrator" else "/ready"
    ready_started = time.monotonic()
    deadline = ready_started + readiness_timeout
    while True:
        status, ready = _http_get(f"{url}{ready_endpoint}", min(timeout, 1.5))
        ready_ok = status == 200 and (
            name == "narrator" or ready.get("status") == "ready"
        )
        if ready_ok:
            break
        if name != "narrator" and ready.get("status") in {"failed", "missing_artifact"}:
            raise BenchmarkError(f"{name}_model_load_failed")
        if time.monotonic() >= deadline:
            raise BenchmarkError(f"{name}_not_ready")
        time.sleep(min(0.5, max(0, deadline - time.monotonic())))
    records = _model_records(url, timeout)
    alias, pinned_revision = EXPECTED[name]
    match = next((item for item in records if item.get("id") == alias), None)
    if match is None:
        raise BenchmarkError(f"{name}_model_alias_mismatch")
    reported_revision = match.get("revision")
    if name != "narrator" and reported_revision != pinned_revision:
        raise BenchmarkError(f"{name}_revision_mismatch")
    return {
        "alias": alias,
        "reported_revision": reported_revision if isinstance(reported_revision, str) else None,
        "expected_revision": pinned_revision,
        "revision_verified": reported_revision == pinned_revision,
        "readiness_endpoint": ready_endpoint,
        "ready": True,
        "readiness_wait_seconds": round(time.monotonic() - ready_started, 3),
    }


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return round(ordered[rank - 1], 3)


def summarize_records(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(record["mode"], []).append(record)
    summaries: dict[str, dict[str, Any]] = {}
    for mode, items in grouped.items():
        elapsed = [float(item["total_ms"]) for item in items if isinstance(item.get("total_ms"), (int, float))]
        successes = sum(item.get("success") is True for item in items)
        tokens = sum(int(item.get("completion_tokens", 0) or 0) for item in items)
        batches: dict[str, float] = {}
        for item in items:
            batch_id = item.get("batch_id")
            batch_elapsed = item.get("batch_elapsed_ms")
            if isinstance(batch_id, str) and isinstance(batch_elapsed, (int, float)):
                batches[batch_id] = max(batches.get(batch_id, 0), float(batch_elapsed))
        elapsed_seconds = (sum(batches.values()) if batches else sum(elapsed)) / 1000
        stages = sorted({stage for item in items for stage in (item.get("stages_ms") or {})})
        summaries[mode] = {
            "requests": len(items),
            "successes": successes,
            "errors": len(items) - successes,
            "success_rate": round(successes / len(items), 4) if items else None,
            "total_ms": {"p50": percentile(elapsed, 0.50), "p95": percentile(elapsed, 0.95)},
            "stages_ms": {
                stage: {
                    "p50": percentile([float(item["stages_ms"][stage]) for item in items if stage in item.get("stages_ms", {})], 0.50),
                    "p95": percentile([float(item["stages_ms"][stage]) for item in items if stage in item.get("stages_ms", {})], 0.95),
                }
                for stage in stages
            },
            "completion_tokens": tokens,
            "completion_tokens_per_second": round(tokens / elapsed_seconds, 3) if elapsed_seconds else None,
            "timeout_errors": sum("timeout" in str(item.get("error_category", "")) for item in items),
        }
    return summaries


def sanitized_record(
    *, mode: str, question: dict[str, Any], success: bool, total_ms: float,
    stages_ms: dict[str, float], error_category: str | None, completion_tokens: int,
) -> dict[str, Any]:
    request_material = json.dumps(
        {"query": question.get("query"), "candidates": question.get("candidates")},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "mode": mode,
        "question_id": question["id"],
        "request_sha256": hashlib.sha256(request_material).hexdigest(),
        "success": success,
        "error_category": error_category,
        "total_ms": round(total_ms, 3),
        "stages_ms": {key: round(value, 3) for key, value in stages_ms.items()},
        "completion_tokens": completion_tokens,
    }


class ReadinessMonitor:
    def __init__(self, urls: dict[str, str], timeout: float, interval: float = 1.0):
        self.urls = urls
        self.timeout = timeout
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.readiness_samples: list[dict[str, Any]] = []
        self.worker_metrics: dict[str, list[dict[str, Any]]] = {"embed": [], "rerank": []}
        self._ever_ready: dict[str, bool] = {name: False for name in urls}
        self._readiness_failure_count = 0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._sample, name="benchmark-readiness", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, 3 * self.interval))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            status = [*self.readiness_samples]
            metrics = {key: [*values] for key, values in self.worker_metrics.items()}
            readiness_failures = self._readiness_failure_count
        return {
            "readiness_observations": len(status),
            "readiness_failures": readiness_failures,
            "worker_metrics_samples": {
                key: len(values) for key, values in metrics.items()
            },
            "last_worker_metrics": {
                key: values[-1] if values else None for key, values in metrics.items()
            },
            "aggregate_worker_counters": {
                key: {
                    field: max((item.get(field, 0) for item in values), default=None)
                    for field in ("accepted_total", "completed_total", "failed_total", "timed_out_total", "rejected_total")
                }
                for key, values in metrics.items()
            },
        }

    def _sample(self) -> None:
        while not self._stop.is_set():
            sampled_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
            states: dict[str, Any] = {"sampled_at": sampled_at}
            for name, url in self.urls.items():
                ready = False
                error_category = None
                try:
                    path = "/health" if name == "narrator" else "/ready"
                    status, response = _http_get(f"{url}{path}", min(self.timeout, 1.5))
                    ready = status == 200 and (name == "narrator" or response.get("status") == "ready")
                except BenchmarkError as exc:
                    error_category = exc.category
                states[name] = {"ready": ready, "error_category": error_category}
                if ready:
                    self._ever_ready[name] = True
                elif self._ever_ready[name]:
                    with self._lock:
                        self._readiness_failure_count += 1
                if name in self.worker_metrics and ready:
                    try:
                        metrics = _json_request("GET", f"{url}/metrics", None, self.timeout)
                        safe = {
                            field: metrics.get(field)
                            for field in ("accepted_total", "completed_total", "failed_total", "timed_out_total", "rejected_total", "active_requests", "queued_requests", "outstanding_requests", "max_outstanding", "saturated", "stuck_inference")
                            if isinstance(metrics.get(field), (int, bool))
                        }
                        with self._lock:
                            self.worker_metrics[name].append(safe)
                    except BenchmarkError:
                        pass
            with self._lock:
                self.readiness_samples.append(states)
            self._stop.wait(self.interval)


def _read_runtime_identities() -> dict[str, Any]:
    identities: dict[str, Any] = {}
    for service, (project, compose_service) in DockerResourceSampler.PROJECTS.items():
        try:
            listing = subprocess.run(
                [
                    "docker", "ps", "--all", "--quiet",
                    "--filter", f"label=com.docker.compose.project={project}",
                    "--filter", f"label=com.docker.compose.service={compose_service}",
                ],
                capture_output=True, text=True, timeout=3, check=False, shell=False,
            )
            ids = [value.strip() for value in listing.stdout.splitlines() if value.strip()]
            if listing.returncode != 0 or len(ids) != 1:
                identities[service] = {"availability": "unavailable", "reason": "container_identity_unavailable"}
                continue
            inspected = subprocess.run(
                ["docker", "inspect", ids[0]],
                capture_output=True, text=True, timeout=3, check=False, shell=False,
            )
            records = json.loads(inspected.stdout) if inspected.returncode == 0 else []
            record = records[0] if isinstance(records, list) and len(records) == 1 and isinstance(records[0], dict) else {}
            config = record.get("Config") if isinstance(record.get("Config"), dict) else {}
            labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
            state = record.get("State") if isinstance(record.get("State"), dict) else {}
            if labels.get("com.docker.compose.project") != project or labels.get("com.docker.compose.service") != compose_service:
                identities[service] = {"availability": "unavailable", "reason": "compose_identity_mismatch"}
                continue
            identities[service] = {
                "availability": "available",
                "compose_project": project,
                "compose_service": compose_service,
                "container_id": ids[0],
                "container_name": str(record.get("Name", "")).lstrip("/"),
                "image_ref": config.get("Image") if isinstance(config.get("Image"), str) else None,
                "image_id": record.get("Image") if isinstance(record.get("Image"), str) else None,
                "runtime_state": state.get("Status") if isinstance(state.get("Status"), str) else "unknown",
            }
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, IndexError, TypeError):
            identities[service] = {"availability": "unavailable", "reason": "docker_unavailable"}
    return identities


class DockerResourceSampler:
    """Sample fixed project containers with `docker stats`; missing metrics stay unavailable."""

    PROJECTS: ClassVar[dict[str, tuple[str, str]]] = {
        "narrator": ("local-ai-api-lite", "vllm"),
        "embed": ("local-ai-api-lite-retrieval", "embed"),
        "rerank": ("local-ai-api-lite-retrieval", "rerank"),
    }

    def __init__(self, interval: float = 1.0, dashboard_pid: int | None = None):
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.samples: dict[str, list[dict[str, float]]] = {name: [] for name in self.PROJECTS}
        self.docker_available: bool | None = None
        self.host_ram_samples: list[float] = []
        self.host_ram_totals: list[float] = []
        self.host_ram_available: list[float] = []
        self.gpu_samples: list[dict[str, float]] = []
        self.runtime_identities: dict[str, Any] = {}
        self.dashboard_pid = dashboard_pid
        self.dashboard_samples: list[dict[str, float]] = []
        self._next_identity_refresh = 0.0

    def start(self) -> None:
        self.refresh_runtime_identities()
        self._thread = threading.Thread(target=self._sample, name="benchmark-resources", daemon=True)
        self._thread.start()

    def refresh_runtime_identities(self) -> None:
        self.runtime_identities = _read_runtime_identities()
        self._next_identity_refresh = time.monotonic() + 2.0

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(2.0, 3 * self.interval))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            output = {}
            for service, values in self.samples.items():
                output[service] = {
                    "samples": len(values),
                    "cpu_percent_peak": max((item["cpu_percent"] for item in values), default=None),
                    "memory_used_bytes_peak": max((item["memory_used_bytes"] for item in values), default=None),
                    "memory_limit_bytes": max((item["memory_limit_bytes"] for item in values), default=None),
                }
            host_ram = list(self.host_ram_samples)
            host_ram_total = list(self.host_ram_totals)
            host_ram_available = list(self.host_ram_available)
            gpu = list(self.gpu_samples)
            dashboard = list(self.dashboard_samples)
            docker_available = self.docker_available
        return {
            "docker_available": docker_available,
            "runtime_identities": {
                service: {key: value for key, value in identity.items() if key != "container_id"}
                for service, identity in self.runtime_identities.items()
            },
            "containers": output,
            "host_ram_used_bytes_peak": max(host_ram, default=None),
            "host_ram_total_bytes": max(host_ram_total, default=None),
            "host_ram_available_bytes_min": min(host_ram_available, default=None),
            "dashboard_process": {
                "pid": self.dashboard_pid,
                "samples": len(dashboard),
                "cpu_percent_peak": max((item["cpu_percent"] for item in dashboard), default=None),
                "memory_used_bytes_peak": max((item["memory_used_bytes"] for item in dashboard), default=None),
                "availability": "available" if dashboard else "unavailable",
            },
            "gpu_device_wide": {
                "samples": len(gpu),
                "utilization_percent_peak": max((item["utilization_percent"] for item in gpu), default=None),
                "memory_used_bytes_peak": max((item["memory_used_bytes"] for item in gpu), default=None),
                "memory_total_bytes": max((item["memory_total_bytes"] for item in gpu), default=None),
            },
            "host_measurement_note": "Container stats are per-container; GPU values are device-wide and cannot be attributed to one service.",
        }

    def _sample(self) -> None:
        while not self._stop.is_set():
            if time.monotonic() >= self._next_identity_refresh:
                self.refresh_runtime_identities()
            self._sample_docker()
            self._sample_host()
            self._stop.wait(self.interval)

    def _sample_docker(self) -> None:
        containers = {
            service: identity.get("container_id")
            for service, identity in self.runtime_identities.items()
            if identity.get("availability") == "available" and identity.get("runtime_state") == "running"
        }
        active_ids = [value for value in containers.values() if isinstance(value, str)]
        if not active_ids:
            return
        try:
            result = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "{{json .}}", *active_ids],
                capture_output=True, text=True, timeout=3, check=False, shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            with self._lock:
                if self.docker_available is None:
                    self.docker_available = False
            return
        if result.returncode != 0:
            with self._lock:
                if self.docker_available is None:
                    self.docker_available = False
            return
        with self._lock:
            self.docker_available = True
        id_to_service = {value: key for key, value in containers.items()}
        name_to_service = {
            identity.get("container_name"): service
            for service, identity in self.runtime_identities.items()
            if isinstance(identity.get("container_name"), str)
        }
        for line in result.stdout.splitlines():
            try:
                row = json.loads(line)
                service = id_to_service.get(row.get("ID")) or id_to_service.get(row.get("Id"))
                if service is None:
                    service = name_to_service.get(str(row.get("Name", "")).lstrip("/"))
                cpu = _parse_float(str(row.get("CPUPerc", "")).removesuffix("%"))
                memory_parts = str(row.get("MemUsage", "")).split(" / ", maxsplit=1)
                memory = _parse_memory(memory_parts[0]) if memory_parts else None
                memory_limit = _parse_memory(memory_parts[1]) if len(memory_parts) == 2 else None
            except (TypeError, json.JSONDecodeError, AttributeError):
                continue
            if service is not None and cpu is not None and memory is not None and memory_limit is not None:
                with self._lock:
                    self.samples[service].append({"cpu_percent": cpu, "memory_used_bytes": memory, "memory_limit_bytes": memory_limit})

    def _sample_host(self) -> None:
        try:
            import psutil

            memory = psutil.virtual_memory()
            with self._lock:
                self.host_ram_samples.append(float(memory.used))
                self.host_ram_totals.append(float(memory.total))
                self.host_ram_available.append(float(memory.available))
        except (ImportError, OSError, AttributeError):
            pass
        if self.dashboard_pid is not None:
            try:
                import psutil

                process = psutil.Process(self.dashboard_pid)
                with self._lock:
                    self.dashboard_samples.append({
                        "cpu_percent": float(process.cpu_percent(interval=None)),
                        "memory_used_bytes": float(process.memory_info().rss),
                    })
            except (ImportError, OSError, AttributeError, ValueError):
                pass
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2, check=False, shell=False,
            )
            if result.returncode != 0:
                return
            first = next(csv.reader(result.stdout.splitlines()), [])
            if len(first) != 3:
                return
            utilization = _parse_float(first[0])
            memory_mib = _parse_float(first[1])
            total_mib = _parse_float(first[2])
            if utilization is None or memory_mib is None or total_mib is None:
                return
            with self._lock:
                self.gpu_samples.append({
                    "utilization_percent": utilization,
                    "memory_used_bytes": memory_mib * 1024 * 1024,
                    "memory_total_bytes": total_mib * 1024 * 1024,
                })
        except (OSError, subprocess.SubprocessError, ValueError):
            pass


def _parse_float(value: str) -> float | None:
    try:
        parsed = float(value.strip())
        return parsed if math.isfinite(parsed) and parsed >= 0 else None
    except (TypeError, ValueError):
        return None


def _parse_memory(value: str) -> float | None:
    units = {"B": 1, "kB": 1000, "KB": 1000, "KiB": 1024, "MB": 1000**2, "MiB": 1024**2, "GB": 1000**3, "GiB": 1024**3}
    normalized = value.strip()
    for unit in sorted(units, key=len, reverse=True):
        if normalized.endswith(unit):
            amount = _parse_float(normalized[: -len(unit)].strip())
            return amount * units[unit] if amount is not None else None
    return None


class ReportWriter:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    def write(self, records: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with (self.output_dir / "results.jsonl").open("w", encoding="utf-8", newline="\n") as stream:
            for item in records:
                stream.write(json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n")
        summaries = summarize_records(records)
        report = {"metadata": metadata, "summary": summaries}
        (self.output_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        lines = [
            "# Synthetic inference benchmark",
            "",
            "This report measures API transport and serving performance with invented fixture text. It does not measure historical retrieval relevance or answer accuracy.",
            "",
            f"- Run mode: `{metadata.get('run_mode')}`",
            f"- Fixture SHA-256: `{metadata.get('fixture_sha256')}`",
            f"- Prompt version: `{metadata.get('prompt_version')}`",
            f"- Concurrency: `{metadata.get('concurrency')}`",
            f"- Python/platform: `{metadata.get('python_version')}` / `{metadata.get('platform')}`",
            f"- Service identities: `{json.dumps(metadata.get('services', {}), sort_keys=True)}`",
            f"- Resource sample: `{json.dumps(metadata.get('resources', {}), sort_keys=True)}`",
            "",
            "| Mode | Requests | Success | Errors | Total p50 / p95 ms | Tokens/s |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for mode, summary in summaries.items():
            total = summary["total_ms"]
            lines.append(f"| {mode} | {summary['requests']} | {summary['successes']} | {summary['errors']} | {total['p50']} / {total['p95']} | {summary['completion_tokens_per_second']} |")
        lines.extend(["", "See `report.json` for per-stage summaries and `results.jsonl` for redacted request-level traces.", ""])
        (self.output_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


class BenchmarkRunner:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.urls = {"embed": args.embed_url.rstrip("/"), "rerank": args.rerank_url.rstrip("/"), "narrator": args.narrator_url.rstrip("/")}
        self._deadline = time.monotonic() + args.max_duration_seconds
        self._records: list[dict[str, Any]] = []
        self._batch_number = 0

    def remaining(self) -> float:
        value = self._deadline - time.monotonic()
        if value <= 0:
            raise BenchmarkError("benchmark_time_budget_exceeded")
        return min(self.args.timeout_seconds, value)

    def request(self, method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return _json_request(method, url, payload, self.remaining())

    def _embedding(self, question: dict[str, Any]) -> tuple[list[float], list[list[float]]]:
        query_response = self.request("POST", f"{self.urls['embed']}/v1/embeddings", {
            "model": "ddu-embed", "input": question["query"], "input_type": "query",
        })
        document_response = self.request("POST", f"{self.urls['embed']}/v1/embeddings", {
            "model": "ddu-embed", "input": [item["text"] for item in question["candidates"]], "input_type": "document",
        })
        try:
            query_vector = query_response["data"][0]["embedding"]
            doc_vectors = [item["embedding"] for item in document_response["data"]]
            if not isinstance(query_vector, list) or not isinstance(doc_vectors, list) or len(doc_vectors) != len(question["candidates"]):
                raise ValueError
            return query_vector, doc_vectors
        except (KeyError, IndexError, TypeError, ValueError):
            raise BenchmarkError("invalid_embedding_response") from None

    @staticmethod
    def _dense_rank(question: dict[str, Any], query_vector: list[float], doc_vectors: list[list[float]]) -> list[dict[str, str]]:
        if any(len(vector) != len(query_vector) for vector in doc_vectors):
            raise BenchmarkError("embedding_dimension_mismatch")
        ranked = sorted(
            zip(question["candidates"], doc_vectors, strict=True),
            key=lambda pair: -sum(float(a) * float(b) for a, b in zip(query_vector, pair[1], strict=True)),
        )
        return [candidate for candidate, _vector in ranked]

    def _rerank(self, question: dict[str, Any], candidates: list[dict[str, str]]) -> list[dict[str, str]]:
        response = self.request("POST", f"{self.urls['rerank']}/v1/rerank", {
            "model": "ddu-rerank", "query": question["query"],
            "documents": [item["text"] for item in candidates], "top_n": len(candidates),
        })
        try:
            indexes = [int(result["index"]) for result in response["results"]]
            if sorted(indexes) != list(range(len(candidates))):
                raise ValueError
            return [candidates[index] for index in indexes]
        except (KeyError, TypeError, ValueError):
            raise BenchmarkError("invalid_rerank_response") from None

    def _generate(self, question: dict[str, Any], candidates: list[dict[str, str]]) -> int:
        fixture = self.fixture
        candidate_context = "\n".join(f"[{item['id']}] {item['text']}" for item in candidates)
        prompt = f"Question: {question['query']}\nSynthetic candidates:\n{candidate_context}\nReturn the answer and evidence_ids as JSON."
        response = self.request("POST", f"{self.urls['narrator']}/v1/chat/completions", {
            "model": "ddu-narrator",
            "messages": [
                {"role": "system", "content": fixture["system_prompt"]},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "max_tokens": 160,
            "seed": 42,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "synthetic_answer", "strict": True, "schema": SCHEMA},
            },
        })
        try:
            usage = response.get("usage", {})
            tokens = usage.get("completion_tokens", 0) if isinstance(usage, dict) else 0
            content = response["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError
            parsed = json.loads(content)
            if (
                not isinstance(parsed, dict)
                or set(parsed) != {"answer", "evidence_ids"}
                or not isinstance(parsed["answer"], str)
                or not isinstance(parsed["evidence_ids"], list)
                or not all(isinstance(item, str) for item in parsed["evidence_ids"])
            ):
                raise ValueError
            return int(tokens) if isinstance(tokens, (int, float)) and tokens >= 0 else 0
        except (KeyError, IndexError, TypeError, ValueError):
            raise BenchmarkError("invalid_generation_response") from None

    def _run_one(self, mode: str, question: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        stages: dict[str, float] = {}
        completion_tokens = 0
        try:
            if mode == "embedding-only":
                t = time.perf_counter(); self._embedding(question); stages["embedding"] = (time.perf_counter() - t) * 1000
            elif mode == "reranking-only":
                t = time.perf_counter(); self._rerank(question, question["candidates"]); stages["reranking"] = (time.perf_counter() - t) * 1000
            elif mode == "generation-only":
                t = time.perf_counter(); completion_tokens = self._generate(question, question["candidates"]); stages["generation"] = (time.perf_counter() - t) * 1000
            elif mode in {"pipeline", "stability"}:
                t = time.perf_counter(); query_vector, doc_vectors = self._embedding(question); stages["embedding"] = (time.perf_counter() - t) * 1000
                t = time.perf_counter(); dense_candidates = self._dense_rank(question, query_vector, doc_vectors); stages["dense_rank"] = (time.perf_counter() - t) * 1000
                t = time.perf_counter(); reranked = self._rerank(question, dense_candidates); stages["reranking"] = (time.perf_counter() - t) * 1000
                t = time.perf_counter(); completion_tokens = self._generate(question, reranked); stages["generation"] = (time.perf_counter() - t) * 1000
            else:
                raise BenchmarkError("invalid_benchmark_mode")
            category = None
            success = True
        except BenchmarkError as exc:
            category = exc.category
            success = False
        except (ArithmeticError, TypeError, ValueError):
            category = "invalid_model_response"
            success = False
        total_ms = (time.perf_counter() - started) * 1000
        return sanitized_record(
            mode=mode, question=question, success=success, total_ms=total_ms,
            stages_ms=stages, error_category=category, completion_tokens=completion_tokens,
        )

    def _run_batch(self, mode: str, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self._batch_number += 1
        batch_id = f"{mode}-{self._batch_number}"
        started = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.args.concurrency) as executor:
            futures = [executor.submit(self._run_one, mode, question) for question in questions]
            records = [future.result() for future in futures]
        elapsed_ms = (time.perf_counter() - started) * 1000
        for record in records:
            record["batch_id"] = batch_id
            record["batch_elapsed_ms"] = round(elapsed_ms, 3)
        self._records.extend(records)
        return records

    def execute(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        self.fixture = fixture
        questions = fixture["questions"][: self.args.max_queries] * self.args.repeats
        started_at = datetime.now(UTC)
        monitor = ReadinessMonitor(self.urls, min(self.args.timeout_seconds, 2.0), interval=1.0)
        resources = DockerResourceSampler(interval=1.0, dashboard_pid=self.args.dashboard_pid)
        monitor.start()
        resources.start()
        startup_started = time.monotonic()
        startup_checks: dict[str, dict[str, Any]] = {}
        stability_stop_reason = None
        try:
            for name, url in self.urls.items():
                startup_checks[name] = verify_service(
                    name,
                    url,
                    self.args.timeout_seconds,
                    readiness_timeout=(self.args.readiness_timeout_seconds if self.args.run_mode == "cold" else 0),
                )
            startup_readiness_wait = time.monotonic() - startup_started
            self._deadline = time.monotonic() + self.args.max_duration_seconds
            resources.refresh_runtime_identities()
            if self.args.warmup:
                for question in questions[: self.args.warmup]:
                    warmup = self._run_one("pipeline", question)
                    if not warmup["success"]:
                        raise BenchmarkError("warmup_failed")
            for mode in ("embedding-only", "reranking-only", "generation-only", "pipeline"):
                records = self._run_batch(mode, questions)
                if any(not item["success"] for item in records):
                    break
            if self.args.stability_seconds and not any(not item["success"] for item in self._records):
                stability_deadline = min(
                    self._deadline,
                    time.monotonic() + self.args.stability_seconds,
                )
                while time.monotonic() < stability_deadline:
                    records = self._run_batch("stability", fixture["questions"][: self.args.max_queries])
                    if any(not item["success"] for item in records):
                        stability_stop_reason = "request_failed"
                        break
                    if monitor.snapshot()["readiness_failures"]:
                        stability_stop_reason = "service_readiness_degraded"
                        break
                if stability_stop_reason is None:
                    stability_stop_reason = "duration_reached"
        finally:
            monitor.stop()
            resources.stop()
        service_checks = {}
        for name, url in self.urls.items():
            try:
                service_checks[name] = verify_service(name, url, self.args.timeout_seconds)
            except BenchmarkError as exc:
                service_checks[name] = {"ready": False, "error_category": exc.category}
        duration_seconds = max(0.001, (datetime.now(UTC) - started_at).total_seconds())
        metadata = {
            "started_at": started_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "duration_seconds": round(duration_seconds, 3),
            "run_mode": self.args.run_mode,
            "concurrency": self.args.concurrency,
            "max_queries": self.args.max_queries,
            "repeats": self.args.repeats,
            "stability_seconds_requested": self.args.stability_seconds,
            "stability_stop_reason": stability_stop_reason,
            "warmup_pipeline_requests": min(self.args.warmup, len(questions)),
            "startup_readiness_wait_seconds": {
                "total": round(startup_readiness_wait, 3),
                "narrator": startup_checks.get("narrator", {}).get("readiness_wait_seconds"),
                "embed": startup_checks.get("embed", {}).get("readiness_wait_seconds"),
                "rerank": startup_checks.get("rerank", {}).get("readiness_wait_seconds"),
            },
            "timeout_seconds": self.args.timeout_seconds,
            "fixture_sha256": sha256_file(FIXTURE_PATH),
            "fixture_version": self.fixture["fixture_version"],
            "prompt_version": self.fixture["prompt_version"],
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "services": {"startup": startup_checks, "after_run": service_checks},
            "expected_container_images": IMAGE_REFS,
            "runtime_container_identities": resources.snapshot()["runtime_identities"],
            "readiness": monitor.snapshot(),
            "resources": resources.snapshot(),
            "client_requests_per_second": round(len(self._records) / duration_seconds, 3),
            "measurement_note": "Cold process/model loading is not triggered by this runner. For cold measurements, start the runner first and issue a separately authorized Compose start from another terminal; warm mode measures already-ready services.",
        }
        return self._records, metadata


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--embed-url", default=EMBED_URL)
    parser.add_argument("--rerank-url", default=RERANK_URL)
    parser.add_argument("--narrator-url", default=NARRATOR_URL)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--run-mode", choices=("cold", "warm"), default="warm")
    parser.add_argument("--concurrency", type=int, choices=(1, 2), default=1)
    parser.add_argument("--max-queries", type=int, choices=tuple(range(1, 4)), default=3)
    parser.add_argument("--repeats", type=int, choices=tuple(range(1, 5)), default=1)
    parser.add_argument("--warmup", type=int, choices=tuple(range(4)), default=1)
    parser.add_argument("--timeout-seconds", type=float, choices=(10, 20, 30, 60), default=30)
    parser.add_argument("--readiness-timeout-seconds", type=int, choices=(30, 60, 180, 300, 600), default=300)
    parser.add_argument("--max-duration-seconds", type=int, choices=(60, 120, 300, 600, 1800), default=120)
    parser.add_argument("--stability-seconds", type=int, choices=(0, 60, 300, 600, 1800), default=0)
    parser.add_argument("--dashboard-pid", type=int, default=None, help="optional dashboard backend process ID for separate CPU/RAM sampling")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dashboard_pid is not None and args.dashboard_pid <= 0:
        raise SystemExit("--dashboard-pid must be a positive process ID")
    if args.stability_seconds > args.max_duration_seconds:
        raise SystemExit("--stability-seconds cannot exceed --max-duration-seconds")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_dir or ROOT / "artifacts" / "retrieval-benchmark" / stamp
    runner = BenchmarkRunner(args)
    try:
        records, metadata = runner.execute()
    except BenchmarkError as exc:
        # Metadata-only failure record; never persist request text or upstream response bodies.
        metadata = {"error_category": exc.category, "run_mode": args.run_mode, "fixture_sha256": sha256_file(FIXTURE_PATH)}
        records = runner._records
        ReportWriter(output_dir).write(records, metadata)
        print(f"Benchmark could not complete ({exc.category}); report: {output_dir}", file=sys.stderr)
        return 2
    ReportWriter(output_dir).write(records, metadata)
    summaries = summarize_records(records)
    failed = any(value["errors"] for value in summaries.values()) or metadata["readiness"]["readiness_failures"] > 0
    print(f"Synthetic benchmark report: {output_dir}")
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
