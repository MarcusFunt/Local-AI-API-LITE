from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .auth import AuthService, require_session
from .docker_status import DockerReader
from .paths import data_directory
from .persistence import MetadataStore
from .profiles import DEFAULT_PROFILES_PATH, load_profiles, public_profile
from .routes.auth import router as auth_router
from .telemetry import read_host_telemetry

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FRONTEND_DIST = ROOT / "dashboard" / "frontend" / "dist"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _unavailable_docker() -> dict[str, Any]:
    return {
        "availability": "unavailable",
        "inference_state": "Unknown",
        "container": None,
        "configuration": None,
        "active_profile": None,
        "last_health_probe_at": None,
        "last_successful_health_probe_at": None,
        "last_failure_reason": "Docker status unavailable",
        "cache_path": None,
    }


def create_app(
    docker_reader: Any | None = None,
    telemetry_reader: Callable[..., dict[str, Any]] | None = None,
    profiles_path: Path | None = None,
    frontend_dist: Path | None = None,
    metadata_path: Path | None = None,
    auth_service: AuthService | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Local-AI-API-LITE Dashboard",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.docker_reader = docker_reader or DockerReader(docker_path=shutil.which("docker"))
    app.state.telemetry_reader = telemetry_reader or read_host_telemetry
    app.state.profiles_path = profiles_path or DEFAULT_PROFILES_PATH
    app.state.frontend_dist = frontend_dist or DEFAULT_FRONTEND_DIST
    store = MetadataStore(metadata_path or data_directory() / "metadata.sqlite3")
    app.state.auth_service = auth_service or AuthService(store)
    app.include_router(auth_router)

    def docker_snapshot() -> dict[str, Any]:
        try:
            return app.state.docker_reader.read()
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError):
            return _unavailable_docker()

    def telemetry_snapshot(cache_path: str | None = None) -> dict[str, Any]:
        try:
            return app.state.telemetry_reader(cache_path=cache_path)
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError):
            return {
                "cpu_percent": None,
                "ram_used_bytes": None,
                "ram_total_bytes": None,
                "cache_disk_free_bytes": None,
                "gpu": {
                    "available": False,
                    "scope": "device-wide",
                    "reason": "Host telemetry unavailable",
                    "utilization_percent": None,
                    "memory_used_bytes": None,
                    "memory_total_bytes": None,
                    "memory_free_bytes": None,
                    "temperature_c": None,
                    "power_watts": None,
                },
            }

    def profile_registry() -> list[dict[str, Any]]:
        return load_profiles(app.state.profiles_path)

    @app.get("/api/control/v1/controller/health")
    def controller_health() -> dict[str, str]:
        return {"status": "healthy", "checked_at": _now()}

    @app.get("/api/control/v1/status", dependencies=[Depends(require_session)])
    def status() -> dict[str, Any]:
        docker = docker_snapshot()
        telemetry = telemetry_snapshot(docker.get("cache_path"))
        try:
            profiles = profile_registry()
            profiles_by_id = {profile["id"]: profile for profile in profiles}
            profile_id = docker.get("active_profile")
            active_profile = public_profile(profiles_by_id[profile_id]) if profile_id in profiles_by_id else None
        except (ValueError, TypeError):
            active_profile = None
        return {
            "refreshed_at": _now(),
            "controller": {"state": "Ready"},
            "inference": {
                "state": docker.get("inference_state", "Unknown"),
                "availability": docker.get("availability", "unavailable"),
                "container": docker.get("container"),
                "configuration": docker.get("configuration"),
                "last_health_probe_at": docker.get("last_health_probe_at"),
                "last_successful_health_probe_at": docker.get("last_successful_health_probe_at"),
                "last_failure_reason": docker.get("last_failure_reason"),
            },
            "active_profile": active_profile,
            "telemetry": telemetry,
        }

    @app.get("/api/control/v1/telemetry", dependencies=[Depends(require_session)])
    def telemetry() -> dict[str, Any]:
        docker = docker_snapshot()
        return {"refreshed_at": _now(), "telemetry": telemetry_snapshot(docker.get("cache_path"))}

    @app.get("/api/control/v1/profiles", dependencies=[Depends(require_session)])
    def profiles() -> dict[str, Any]:
        try:
            return {"profiles": [public_profile(profile) for profile in profile_registry()]}
        except (ValueError, TypeError):
            return JSONResponse(
                status_code=503,
                content={"profiles": [], "message": "The dashboard profile registry is unavailable"},
            )

    dist = app.state.frontend_dist
    if (dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=dist, html=True), name="dashboard")
    else:

        @app.get("/", include_in_schema=False)
        def frontend_not_built() -> JSONResponse:
            return JSONResponse(
                status_code=503,
                content={"message": "Dashboard frontend is not built. Follow dashboard/README.md."},
            )

    return app
