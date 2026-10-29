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
from .compose_runner import ComposeRunner
from .configuration import LocalSettingsStore
from .control_state import ActivityGate, OperationCoordinator
from .databases import DatabaseMonitor
from .docker_status import DockerReader
from .jobs import DockerJobCommandAdapter, JobArtifactStore, JobRunner
from .logs import DockerLogReader
from .paths import data_directory
from .persistence import JOB_SCHEMA, MetadataStore
from .profiles import DEFAULT_PROFILES_PATH, load_profiles, public_profile
from .routes.auth import router as auth_router
from .routes.databases import router as databases_router
from .routes.jobs import router as jobs_router
from .routes.logs import router as logs_router
from .routes.operations import router as operations_router
from .secrets_store import AESGCMCipher, SecretsStore, WindowsDpapiProtector
from .status_cache import StatusCache
from .telemetry import read_host_telemetry

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FRONTEND_DIST = ROOT / "dashboard" / "frontend" / "dist"


class ComposeRunnerProvider:
    """Resolve the explicitly configured project for every fixed Compose operation."""

    timeout = 150

    def __init__(self, settings: LocalSettingsStore) -> None:
        self.settings = settings

    @property
    def project_root(self) -> Path | None:
        return self.settings.load_compose_root()

    def _runner(self) -> ComposeRunner:
        root = self.project_root
        if root is None:
            raise RuntimeError("Compose project is not configured")
        return ComposeRunner(root, timeout=self.timeout)

    def inspect_project(self) -> dict[str, str]:
        return self._runner().inspect_project()

    def stop_vllm(self) -> dict[str, str]:
        return self._runner().stop_vllm()

    def start_vllm(self) -> dict[str, str]:
        return self._runner().start_vllm()

    def configure_profile(self, profile_id: str) -> None:
        self._runner().configure_profile(profile_id)

    def assert_alias_released(self) -> bool:
        return self._runner().assert_alias_released()

    def verify_contract(self, profile_id: str) -> bool:
        return self._runner().verify_contract(profile_id)


class ComposeContractChecker:
    def __init__(self, runner: ComposeRunnerProvider) -> None:
        self.runner = runner

    def check(self, profile_id: str) -> bool:
        return self.runner.verify_contract(profile_id)


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
    status_cache: Any | None = None,
    settings_store: Any | None = None,
    operation_coordinator: Any | None = None,
    operation_runner: Any | None = None,
    contract_checker: Any | None = None,
    job_runner: Any | None = None,
    log_reader: Any | None = None,
    secret_store: Any | None = None,
    database_monitor: Any | None = None,
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
    app.state.local_settings_store = settings_store or LocalSettingsStore()
    app.state.log_reader = log_reader or DockerLogReader(docker_path=shutil.which("docker"))
    app.include_router(auth_router)

    def docker_snapshot(deadline: float) -> dict[str, Any]:
        try:
            return app.state.docker_reader.read(deadline=deadline)
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

    class DashboardStatusReader:
        def read(self, deadline: float) -> dict[str, Any]:
            docker = docker_snapshot(deadline)
            return {
                **docker,
                "telemetry": telemetry_snapshot(docker.get("cache_path")),
            }

    app.state.status_cache = status_cache or StatusCache(
        DashboardStatusReader(),
        max_age=5,
        command_budget=8,
    )
    runner = operation_runner or ComposeRunnerProvider(app.state.local_settings_store)
    checker = contract_checker or ComposeContractChecker(runner)
    activity_gate = ActivityGate(store)
    app.state.operation_coordinator = operation_coordinator or OperationCoordinator(
        settings=app.state.local_settings_store,
        runner=runner,
        status_reader=app.state.status_cache,
        contract_checker=checker,
        gate=activity_gate,
    )
    if job_runner is None:
        job_command_adapter = DockerJobCommandAdapter(
            docker_path=shutil.which("docker"),
            script_root=ROOT / "scripts",
            profiles_path=app.state.profiles_path,
            runtime_inspector=runner,
        )
        job_activity_gate = getattr(app.state.operation_coordinator, "gate", None) or activity_gate
        app.state.job_runner = JobRunner(
            job_activity_gate,
            job_command_adapter,
            JobArtifactStore(),
        )
    else:
        app.state.job_runner = job_runner
    if store.schema_version() < 4:
        store.migrate(4, JOB_SCHEMA)
    app.state.secret_store = secret_store or SecretsStore(
        store,
        store.path.parent / "vault" / "root-key.dpapi",
        WindowsDpapiProtector(),
        AESGCMCipher(),
    )
    if database_monitor is None:
        app.state.database_monitor = DatabaseMonitor(
            app.state.secret_store,
            metadata_store=store,
        )
    else:
        app.state.database_monitor = database_monitor
    app.include_router(operations_router)
    app.include_router(jobs_router)
    app.include_router(logs_router)
    app.include_router(databases_router)
    if operation_coordinator is None:
        try:
            app.state.operation_coordinator.reconcile_after_restart()
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError):
            pass

    def profile_registry() -> list[dict[str, Any]]:
        return load_profiles(app.state.profiles_path)

    @app.get("/api/control/v1/controller/health")
    def controller_health() -> dict[str, str]:
        return {"status": "healthy", "checked_at": _now()}

    @app.get("/api/control/v1/status", dependencies=[Depends(require_session)])
    def status() -> dict[str, Any]:
        snapshot = app.state.status_cache.get_snapshot()
        try:
            profiles = profile_registry()
            profiles_by_id = {profile["id"]: profile for profile in profiles}
            profile_id = snapshot.get("active_profile")
            active_profile = public_profile(profiles_by_id[profile_id]) if profile_id in profiles_by_id else None
        except (ValueError, TypeError):
            active_profile = None
        return {
            "refreshed_at": _now(),
            "controller": {"state": "Ready"},
            "inference": {
                "state": snapshot.get("inference_state", "Unknown"),
                "availability": snapshot.get("availability", "unavailable"),
                "container": snapshot.get("container"),
                "configuration": snapshot.get("configuration"),
                "last_health_probe_at": snapshot.get("last_health_probe_at"),
                "last_successful_health_probe_at": snapshot.get("last_successful_health_probe_at"),
                "last_failure_reason": snapshot.get("last_failure_reason"),
            },
            "active_profile": active_profile,
            "telemetry": snapshot.get("telemetry"),
            "status_cache": _public_cache_state(snapshot),
        }

    @app.get("/api/control/v1/telemetry", dependencies=[Depends(require_session)])
    def telemetry() -> dict[str, Any]:
        snapshot = app.state.status_cache.get_snapshot()
        return {
            "refreshed_at": _now(),
            "telemetry": snapshot.get("telemetry"),
            "status_cache": _public_cache_state(snapshot),
        }

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


def _public_cache_state(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "observed_at": snapshot.get("observed_at"),
        "stale": snapshot.get("stale", True),
        "refreshing": snapshot.get("refreshing", False),
        "last_poll_error": snapshot.get("last_poll_error"),
        "last_poll_error_at": snapshot.get("last_poll_error_at"),
    }
