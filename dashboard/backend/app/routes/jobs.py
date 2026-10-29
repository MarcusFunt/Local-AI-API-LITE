from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.auth import AuthSession, require_csrf, require_session
from app.jobs import JobHistoryFullError, JobValidationError, _sanitize_artifact_record

router = APIRouter(prefix="/api/control/v1/jobs", tags=["jobs"])
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
_REVISION = re.compile(r"^[0-9a-fA-F]{40}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_ERRORS = {
    "timeout",
    "connection_error",
    "http_error",
    "invalid_response",
    "invalid_configuration",
    "invalid_models_response",
    "model_alias_mismatch",
    "request_failed",
    "output_too_large",
    "profile_mismatch",
    "runtime_unavailable",
    "artifact_too_large",
    "artifact_storage_unavailable",
    "activity_conflict",
    "command_failed",
    "cancelled_by_operator",
    "job_runner_error",
}
_OPTIONS = {
    "server-readiness": {"timeout_seconds"},
    "api-smoke": {"case", "timeout_seconds", "max_tokens"},
    "performance-benchmark": {
        "profile_id",
        "concurrency",
        "diagnostic_concurrency",
        "requests_per_concurrency",
        "warmup_per_case",
        "timeout_seconds",
    },
}
_COMPARE_FIELDS = (
    "kind",
    "profile_id",
    "model_revision",
    "engine",
    "engine_image_digest",
    "context_length",
    "max_num_seqs",
    "host.platform",
    "host.architecture",
    "hardware.gpu_model",
    "script_version",
)


class JobStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: Literal["server-readiness", "api-smoke", "performance-benchmark"]
    options: dict[str, Any] = Field(default_factory=dict)


class CleanupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: bool


def _valid_id(value: str) -> bool:
    return bool(_JOB_ID.fullmatch(value))


def _settings_unconfigured(request: Request) -> bool:
    return request.app.state.local_settings_store.load_compose_root() is None


def _public_job(record: Any) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    job_id = record.get("job_id")
    kind = record.get("kind")
    state = record.get("state")
    if (
        not isinstance(job_id, str)
        or not _valid_id(job_id)
        or not isinstance(kind, str)
        or kind not in _OPTIONS
        or not isinstance(state, str)
        or state not in {"queued", "running", "completed", "failed", "cancelled", "rejected"}
    ):
        return None
    options: dict[str, Any] = {}
    raw_options = record.get("options")
    if isinstance(raw_options, dict):
        for key in _OPTIONS[kind] & set(raw_options):
            value = raw_options[key]
            if _public_option_is_valid(key, value):
                options[key] = value
    summary = _public_summary(record.get("summary"))
    artifact_id = record.get("artifact_id")
    if artifact_id != f"{job_id}.jsonl":
        artifact_id = None
    return {
        "job_id": job_id,
        "kind": kind,
        "state": state,
        "created_at": _safe_timestamp(record.get("created_at")),
        "started_at": _safe_timestamp(record.get("started_at")),
        "completed_at": _safe_timestamp(record.get("completed_at")),
        "options": options,
        "summary": summary,
        "error_category": record.get("error_category")
        if isinstance(record.get("error_category"), str) and record.get("error_category") in _ERRORS
        else None,
        "artifact_id": artifact_id,
        "exit_code": record.get("exit_code") if type(record.get("exit_code")) is int else None,
    }


def _public_summary(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    summary: dict[str, Any] = {}
    if type(raw.get("ok")) is bool:
        summary["ok"] = raw["ok"]
    profile_id = raw.get("profile_id")
    if isinstance(profile_id, str) and profile_id in {"baseline-4k-2", "baseline-8k-4"}:
        summary["profile_id"] = profile_id
    revision = raw.get("model_revision")
    if isinstance(revision, str) and _REVISION.fullmatch(revision):
        summary["model_revision"] = revision
    engine = raw.get("engine")
    if isinstance(engine, str) and engine in {"vLLM 0.32.0"}:
        summary["engine"] = engine
    digest = raw.get("engine_image_digest")
    if isinstance(digest, str) and _DIGEST.fullmatch(digest):
        summary["engine_image_digest"] = digest
    for key, low, high in (
        ("context_length", 1, 262144),
        ("max_num_seqs", 1, 128),
        ("concurrency", 1, 16),
        ("request_count", 0, 10000),
        ("success_count", 0, 10000),
        ("error_count", 0, 10000),
    ):
        value = raw.get(key)
        if type(value) is int and low <= value <= high:
            summary[key] = value
    for key in ("duration_seconds",):
        value = raw.get(key)
        if type(value) in {int, float} and 0 <= value <= 1_000_000:
            summary[key] = round(float(value), 3)
    script_version = raw.get("script_version")
    if isinstance(script_version, str) and re.fullmatch(r"[0-9.]{1,20}", script_version):
        summary["script_version"] = script_version
    for key in ("host", "hardware"):
        nested = raw.get(key)
        if isinstance(nested, dict):
            clean: dict[str, str] = {}
            for field in ("platform", "architecture", "gpu_model"):
                value = nested.get(field)
                safe_platform = (
                    field == "platform"
                    and isinstance(value, str)
                    and value in {"Windows", "Linux", "Darwin"}
                )
                safe_arch = (
                    field == "architecture"
                    and isinstance(value, str)
                    and value in {"AMD64", "x86_64", "arm64", "aarch64"}
                )
                safe_gpu = field == "gpu_model" and isinstance(value, str) and re.fullmatch(
                    r"(?:NVIDIA|AMD|Intel|GeForce|Tesla|Quadro|Radeon|Apple)(?:[A-Za-z0-9 _.,()+-]{0,120})",
                    value,
                )
                if safe_platform or safe_arch or safe_gpu:
                    clean[field] = value
            if clean:
                summary[key] = clean
    percentiles = raw.get("latency_percentiles")
    if isinstance(percentiles, dict):
        clean_percentiles = {
            key: round(float(value), 3)
            for key, value in percentiles.items()
            if key in {"p50_seconds", "p95_seconds"}
            and type(value) in {int, float}
            and 0 <= value <= 1_000_000
        }
        if clean_percentiles:
            summary["latency_percentiles"] = clean_percentiles
    telemetry = raw.get("telemetry")
    if isinstance(telemetry, dict):
        clean_telemetry: dict[str, Any] = {}
        for key in ("cpu_percent", "ram_used_bytes", "ram_total_bytes", "cache_disk_free_bytes"):
            value = telemetry.get(key)
            if type(value) in {int, float} and 0 <= value <= 10**16:
                clean_telemetry[key] = value
        gpu = telemetry.get("gpu")
        if isinstance(gpu, dict):
            clean_gpu: dict[str, Any] = {}
            if type(gpu.get("available")) is bool:
                clean_gpu["available"] = gpu["available"]
            for key in (
                "utilization_percent",
                "memory_used_bytes",
                "memory_total_bytes",
                "temperature_c",
                "power_watts",
            ):
                value = gpu.get(key)
                if type(value) in {int, float} and 0 <= value <= 10**16:
                    clean_gpu[key] = value
            if clean_gpu:
                clean_telemetry["gpu"] = clean_gpu
        if clean_telemetry:
            summary["telemetry"] = clean_telemetry
    return summary


def _safe_timestamp(value: Any) -> str | None:
    return value if isinstance(value, str) and _TIMESTAMP.fullmatch(value) else None


def _public_option_is_valid(key: str, value: Any) -> bool:
    if key == "profile_id":
        return isinstance(value, str) and value in {"baseline-4k-2", "baseline-8k-4"}
    if key == "case":
        return isinstance(value, str) and value in {"all", "history", "medical"}
    if key == "diagnostic_concurrency":
        return type(value) is bool
    if key == "concurrency":
        return type(value) is int and value in {1, 2, 4}
    if key == "requests_per_concurrency":
        return type(value) is int and 1 <= value <= 25
    if key == "warmup_per_case":
        return type(value) is int and 0 <= value <= 2
    if key == "max_tokens":
        return type(value) is int and 1 <= value <= 600
    if key == "timeout_seconds":
        return type(value) is int and 1 <= value <= 1800
    return False


def _field(record: dict[str, Any], field: str) -> Any:
    value: Any = record
    for key in field.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value


def _compare(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    differences = []
    for field in _COMPARE_FIELDS:
        left_value = _field(left, field) if field == "kind" else _field(left["summary"], field)
        right_value = _field(right, field) if field == "kind" else _field(right["summary"], field)
        if left_value != right_value or left_value is None:
            differences.append(
                {
                    "field": field,
                    "left": left_value if left_value is not None else "Unavailable",
                    "right": right_value if right_value is not None else "Unavailable",
                }
            )
    comparable = (
        left["state"] == "completed"
        and right["state"] == "completed"
        and not differences
    )
    return {"comparable": comparable, "differences": differences, "left": left, "right": right}


def _missing() -> JSONResponse:
    return JSONResponse(status_code=404, content={"message": "Dashboard job is unavailable"})


@router.get("/history")
def history(
    request: Request,
    _session: Annotated[AuthSession, Depends(require_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> dict[str, Any]:
    records = request.app.state.job_runner.history(limit, offset)
    jobs = [_public_job(record) for record in records[:limit]]
    return {"jobs": [record for record in jobs if record is not None], "limit": limit, "offset": offset}


@router.get("/compare")
def compare(
    request: Request,
    _session: Annotated[AuthSession, Depends(require_session)],
    left_id: str = Query(min_length=32, max_length=32),
    right_id: str = Query(min_length=32, max_length=32),
) -> JSONResponse:
    if not _valid_id(left_id) or not _valid_id(right_id) or left_id == right_id:
        raise HTTPException(status_code=422, detail="Two distinct dashboard job IDs are required")
    runner = request.app.state.job_runner
    left = _public_job(runner.status(left_id))
    right = _public_job(runner.status(right_id))
    if left is None or right is None:
        return _missing()
    return JSONResponse(content=_compare(left, right))


@router.get("/cleanup/preview")
def cleanup_preview(
    request: Request,
    _session: Annotated[AuthSession, Depends(require_session)],
) -> dict[str, Any]:
    return request.app.state.job_runner.preview_cleanup()


@router.post("/cleanup")
def cleanup(
    body: CleanupRequest,
    request: Request,
    _session: Annotated[AuthSession, Depends(require_csrf)],
) -> JSONResponse:
    if not body.confirm:
        raise HTTPException(status_code=409, detail="Explicit cleanup confirmation is required")
    try:
        result = request.app.state.job_runner.cleanup_history(confirm=True)
    except Exception:  # noqa: BLE001 - cleanup details can include local file paths.
        raise HTTPException(status_code=503, detail="Job history cleanup is unavailable") from None
    return JSONResponse(content=result)


@router.post("/start")
def start(
    body: JobStartRequest,
    request: Request,
    _session: Annotated[AuthSession, Depends(require_csrf)],
) -> JSONResponse:
    if _settings_unconfigured(request):
        return JSONResponse(
            status_code=409,
            content={"message": "Configure a stable local Compose project before running dashboard jobs."},
        )
    try:
        record = request.app.state.job_runner.start(body.job_id, body.options)
    except JobValidationError:
        raise HTTPException(status_code=422, detail="Job options are outside the fixed registry") from None
    except JobHistoryFullError:
        raise HTTPException(status_code=409, detail="Preview and confirm cleanup before starting more jobs") from None
    except Exception:  # noqa: BLE001 - never expose command adapter output or paths.
        raise HTTPException(status_code=503, detail="Dashboard job could not be started") from None
    public = _public_job(record)
    if public is None:
        raise HTTPException(status_code=503, detail="Dashboard job state is unavailable")
    return JSONResponse(status_code=409 if public["state"] == "rejected" else 202, content=public)


@router.post("/{job_id}/cancel")
def cancel(
    job_id: str,
    request: Request,
    _session: Annotated[AuthSession, Depends(require_csrf)],
) -> JSONResponse:
    if not _valid_id(job_id):
        return _missing()
    try:
        accepted = request.app.state.job_runner.cancel(job_id)
        record = request.app.state.job_runner.status(job_id)
    except KeyError:
        return _missing()
    public = _public_job(record)
    if public is None:
        return _missing()
    return JSONResponse(content={"cancel_requested": bool(accepted), "job": public})


@router.get("/{job_id}/artifact")
def artifact(
    job_id: str,
    request: Request,
    _session: Annotated[AuthSession, Depends(require_session)],
) -> JSONResponse:
    if not _valid_id(job_id):
        return _missing()
    runner = request.app.state.job_runner
    try:
        record = _public_job(runner.status(job_id))
        if record is None or record["artifact_id"] is None:
            return _missing()
        text = runner.artifact_store.read_text(record["artifact_id"])
    except (KeyError, OSError):
        return _missing()
    records = []
    for line in text.splitlines()[:500]:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            records.append(_sanitize_artifact_record(parsed))
    return JSONResponse(content={"job_id": job_id, "records": records})


@router.get("/{job_id}")
def status(
    job_id: str,
    request: Request,
    _session: Annotated[AuthSession, Depends(require_session)],
) -> JSONResponse:
    if not _valid_id(job_id):
        return _missing()
    try:
        record = _public_job(request.app.state.job_runner.status(job_id))
    except KeyError:
        return _missing()
    return JSONResponse(content=record) if record is not None else _missing()
