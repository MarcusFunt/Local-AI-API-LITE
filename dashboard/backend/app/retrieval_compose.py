from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

import yaml

from .retrieval_status import (
    RETRIEVAL_IMAGE_REF,
    RETRIEVAL_NETWORK_NAME,
    RETRIEVAL_PROJECT_NAME,
    SERVICE_SPECS,
)

RETRIEVAL_ROOT_KEYS = {"name", "services", "networks"}
RETRIEVAL_SERVICE_NAMES = {"embed", "rerank", "model-installer"}
_CONTAINER_ID = re.compile(r"^[0-9a-fA-F]{12,64}$")
_MODEL_CACHE_ENV = "${MODEL_CACHE_DIR:?Set MODEL_CACHE_DIR}"
_RESOURCE_LIMITS = {"embed": {"cpus": "4.0", "memory": "5g"}, "rerank": {"cpus": "2.0", "memory": "4g"}}
_RESOURCE_LIMIT_MEMORY_BYTES = {"embed": "5368709120", "rerank": "4294967296"}
_WORKER_ENVIRONMENT = {
    "HF_HOME": "/models",
    "HF_HUB_CACHE": "/models/hub",
    "HF_MODULES_CACHE": "/tmp/hf-modules",
    "HF_HUB_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "OMP_NUM_THREADS": "2",
    "MKL_NUM_THREADS": "2",
}
_PROBE_SCRIPT = (
    "import json,urllib.request,urllib.error\n"
    "def get(p):\n"
    " try:\n"
    "  with urllib.request.urlopen('http://127.0.0.1:8080'+p,timeout=1.5) as r:\n"
    "   return r.status,json.loads(r.read(32768))\n"
    " except urllib.error.HTTPError as e:\n"
    "  try: body=json.loads(e.read(4096))\n"
    "  except Exception: body={}\n"
    "  return e.code,body\n"
    " except Exception: return 0,{}\n"
    "h,_=get('/health'); r,ready=get('/ready'); m,models=get('/v1/models'); "
    "items=models.get('data',[]) if isinstance(models,dict) else []; "
    "model=items[0] if items and isinstance(items[0],dict) else {}; "
    "print(json.dumps({'health_status':h,'ready_status':r,'ready_state':ready.get('status') "
    "if isinstance(ready,dict) else None,'models_status':m,'model_id':model.get('id'),"
    "'revision':model.get('revision')},separators=(',',':')))"
)


class RetrievalComposeError(RuntimeError):
    """A fixed retrieval Compose contract or operation failed safely."""

    _categories: ClassVar[frozenset[str]] = frozenset({
        "retrieval_compose_contract_invalid",
        "retrieval_project_unconfigured",
        "docker_unavailable",
        "docker_timeout",
        "compose_command_failed",
        "compose_identity_mismatch",
        "runtime_identity_mismatch",
        "runtime_privilege_mismatch",
        "host_port_published",
        "network_alias_mismatch",
        "model_cache_mount_mismatch",
        "model_load_failed",
        "model_identity_mismatch",
        "liveness_check_failed",
        "readiness_timeout",
        "retrieval_operation_failed",
    })

    def __init__(self, message: str, category: str = "retrieval_operation_failed") -> None:
        super().__init__(message)
        self.category = category if category in self._categories else "retrieval_operation_failed"


def validate_retrieval_compose_root(project_root: Path) -> Path:
    try:
        root = project_root.expanduser().resolve(strict=True)
        compose_file = root / "compose.yaml"
        env_file = root / ".env"
        if not root.is_dir() or not compose_file.is_file() or not env_file.is_file():
            raise RetrievalComposeError("Retrieval Compose files are unavailable", "retrieval_compose_contract_invalid")
        document = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    except RetrievalComposeError:
        raise
    except (OSError, RuntimeError, yaml.YAMLError) as exc:
        raise RetrievalComposeError("Retrieval Compose files are invalid", "retrieval_compose_contract_invalid") from exc
    _validate_source(document)
    return root


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RetrievalComposeError(message, "retrieval_compose_contract_invalid")


def _validate_source(document: object) -> None:
    _require(isinstance(document, dict) and set(document) == RETRIEVAL_ROOT_KEYS, "Retrieval Compose project structure does not match")
    assert isinstance(document, dict)
    _require(document.get("name") == RETRIEVAL_PROJECT_NAME, "Retrieval Compose project name does not match")
    services = document.get("services")
    _require(isinstance(services, dict) and set(services) == RETRIEVAL_SERVICE_NAMES, "Retrieval Compose service set does not match")
    assert isinstance(services, dict)
    for name in ("embed", "rerank"):
        service = services[name]
        _require(isinstance(service, dict), "Retrieval worker definition is invalid")
        expected_keys = {
            "image", "build", "command", "expose", "environment", "volumes", "read_only",
            "tmpfs", "user", "init", "cap_drop", "security_opt", "stop_grace_period",
            "healthcheck", "logging", "deploy", "networks",
        }
        _require(set(service) == expected_keys, "Retrieval worker has an unreviewed Compose option")
        _require(service.get("image") == RETRIEVAL_IMAGE_REF, "Retrieval worker image tag does not match")
        _require(service.get("build") == {"context": "../..", "dockerfile": "services/retrieval/Dockerfile"}, "Retrieval worker build provenance does not match")
        _require(service.get("command") == ["python", "-m", "retrieval_worker.app", "--role", "embedding" if name == "embed" else "reranking"], "Retrieval worker command does not match")
        _require(service.get("expose") == ["8080"], "Retrieval worker internal port does not match")
        _require(service.get("environment") == _WORKER_ENVIRONMENT, "Retrieval worker environment does not match")
        volumes = service.get("volumes")
        _require(isinstance(volumes, list) and len(volumes) == 1, "Retrieval worker model cache mount does not match")
        _require(volumes[0] == {"type": "bind", "source": _MODEL_CACHE_ENV, "target": "/models", "read_only": True}, "Retrieval worker model cache must remain read-only")
        _require(service.get("read_only") is True and service.get("tmpfs") == ["/tmp:rw,noexec,nosuid,size=64m"], "Retrieval worker filesystem contract does not match")
        _require(service.get("user") == "10001:10001" and service.get("init") is True, "Retrieval worker user or init contract does not match")
        _require(service.get("cap_drop") == ["ALL"] and service.get("security_opt") == ["no-new-privileges:true"], "Retrieval worker privilege contract does not match")
        _require(service.get("stop_grace_period") == "75s", "Retrieval worker stop grace period does not match")
        health = service.get("healthcheck")
        _require(isinstance(health, dict) and set(health) == {"test", "interval", "timeout", "retries", "start_period"}, "Retrieval worker liveness probe does not match")
        _require(health.get("test", [None, None, None, ""])[0:3] == ["CMD", "python", "-c"] and "/health" in health["test"][3], "Retrieval worker health must use /health")
        _require(health.get("interval") == "30s" and health.get("timeout") == "5s" and health.get("retries") == 3 and health.get("start_period") == "30s", "Retrieval worker health timing does not match")
        _require(service.get("logging") == {"options": {"max-size": "10m", "max-file": "3"}}, "Retrieval worker log bounds do not match")
        deploy = service.get("deploy")
        expected_resources = {"resources": {"limits": _RESOURCE_LIMITS[name]}}
        _require(deploy == expected_resources, "Retrieval worker CPU or memory limits do not match")
        networks = service.get("networks")
        aliases = networks.get("inference", {}).get("aliases") if isinstance(networks, dict) else None
        _require(isinstance(networks, dict) and set(networks) == {"inference"} and aliases == [SERVICE_SPECS[name]["alias"]], "Retrieval worker network alias does not match")
    _validate_source_installer(services["model-installer"])
    networks = document.get("networks")
    _require(isinstance(networks, dict) and set(networks) == {"inference"}, "Retrieval Compose network set does not match")
    inference = networks["inference"]
    _require(inference == {"name": RETRIEVAL_NETWORK_NAME, "external": True}, "Retrieval Compose must use the private external inference network")


def _validate_source_installer(service: object) -> None:
    _require(isinstance(service, dict), "Retrieval model installer definition is invalid")
    expected_keys = {"image", "build", "profiles", "entrypoint", "command", "environment", "volumes", "read_only", "tmpfs", "user", "cap_drop", "security_opt"}
    _require(set(service) == expected_keys, "Retrieval installer has an unreviewed Compose option")
    assert isinstance(service, dict)
    _require(service.get("image") == RETRIEVAL_IMAGE_REF, "Retrieval installer image tag does not match")
    _require(service.get("build") == {"context": "../..", "dockerfile": "services/retrieval/Dockerfile"}, "Retrieval installer build provenance does not match")
    _require(service.get("profiles") == ["install"] and service.get("entrypoint") == ["python", "-m", "retrieval_worker.install_models"] and service.get("command") == ["--plan"], "Retrieval installer command is not the explicit plan command")
    _require(service.get("environment") == {"HF_HOME": "/models", "HF_HUB_CACHE": "/models/hub", "HF_HUB_OFFLINE": "0", "HF_HUB_DISABLE_TELEMETRY": "1", "TRANSFORMERS_OFFLINE": "0"}, "Retrieval installer environment does not match")
    _require(service.get("volumes") == [{"type": "bind", "source": _MODEL_CACHE_ENV, "target": "/models", "read_only": False}], "Retrieval installer cache mount does not match")
    _require(service.get("read_only") is True and service.get("tmpfs") == ["/tmp:rw,noexec,nosuid,size=64m"], "Retrieval installer filesystem contract does not match")
    _require(service.get("cap_drop") == ["ALL"] and service.get("security_opt") == ["no-new-privileges:true"], "Retrieval installer privilege contract does not match")
    _require(service.get("user") == "${MODEL_CACHE_UID:-10001}:${MODEL_CACHE_GID:-10001}", "Retrieval installer user contract does not match")


def _validate_rendered_configuration(root: Path, rendered: object) -> None:
    _require(isinstance(rendered, dict) and set(rendered) == RETRIEVAL_ROOT_KEYS, "Resolved retrieval Compose project structure does not match")
    assert isinstance(rendered, dict)
    _require(rendered.get("name") == RETRIEVAL_PROJECT_NAME, "Resolved retrieval Compose project identity does not match")
    services = rendered.get("services")
    _require(isinstance(services, dict) and set(services) == RETRIEVAL_SERVICE_NAMES, "Resolved retrieval service set does not match")
    assert isinstance(services, dict)
    for name in ("embed", "rerank"):
        service = services[name]
        _require(isinstance(service, dict), "Resolved retrieval worker definition is invalid")
        _require(not ({"ports", "gpus", "privileged", "devices", "depends_on", "restart", "network_mode", "pid", "ipc"} & set(service)), "Resolved retrieval worker contains an unsafe runtime option")
        _require(service.get("image") == RETRIEVAL_IMAGE_REF, "Resolved retrieval image does not match")
        _require(service.get("command") == ["python", "-m", "retrieval_worker.app", "--role", "embedding" if name == "embed" else "reranking"], "Resolved retrieval command does not match")
        build = service.get("build")
        _require(isinstance(build, dict) and Path(str(build.get("context", ""))).resolve() == root.resolve().parent.parent and str(build.get("dockerfile", "")).replace("\\", "/").endswith("services/retrieval/Dockerfile"), "Resolved retrieval build provenance does not match")
        _require(service.get("environment") == _WORKER_ENVIRONMENT and service.get("expose") == ["8080"], "Resolved retrieval environment or internal port does not match")
        _require(service.get("read_only") is True and service.get("init") is True and service.get("user") == "10001:10001", "Resolved retrieval worker isolation does not match")
        _require(service.get("cap_drop") == ["ALL"] and service.get("security_opt") == ["no-new-privileges:true"], "Resolved retrieval privilege contract does not match")
        _require(service.get("tmpfs") == ["/tmp:rw,noexec,nosuid,size=64m"], "Resolved retrieval temporary filesystem does not match")
        limits = service.get("deploy", {}).get("resources", {}).get("limits") if isinstance(service.get("deploy"), dict) else None
        _require(
            isinstance(limits, dict)
            and str(limits.get("cpus")) in {
                _RESOURCE_LIMITS[name]["cpus"],
                str(int(float(_RESOURCE_LIMITS[name]["cpus"]))),
            }
            and str(limits.get("memory", "")) == _RESOURCE_LIMIT_MEMORY_BYTES[name],
            "Resolved retrieval resource limits do not match",
        )
        volumes = service.get("volumes")
        _require(isinstance(volumes, list) and len(volumes) == 1 and isinstance(volumes[0], dict) and volumes[0].get("type") == "bind" and volumes[0].get("target") == "/models" and volumes[0].get("read_only") is True and Path(str(volumes[0].get("source", ""))).is_absolute(), "Resolved retrieval model cache mount is not read-only")
        networks = service.get("networks")
        inference = networks.get("inference") if isinstance(networks, dict) else None
        _require(isinstance(inference, dict) and inference.get("aliases") == [SERVICE_SPECS[name]["alias"]], "Resolved retrieval network alias does not match")
    _validate_rendered_installer(root, services["model-installer"])
    rendered_networks = rendered.get("networks")
    inference = rendered_networks.get("inference") if isinstance(rendered_networks, dict) else None
    _require(isinstance(inference, dict) and inference.get("name") == RETRIEVAL_NETWORK_NAME and inference.get("external") is True, "Resolved retrieval network is not the private external network")


def _validate_rendered_installer(root: Path, service: object) -> None:
    _require(isinstance(service, dict), "Resolved retrieval installer is invalid")
    assert isinstance(service, dict)
    _require(not ({"ports", "gpus", "privileged", "devices", "depends_on", "restart", "network_mode", "pid", "ipc"} & set(service)), "Resolved retrieval installer contains an unsafe runtime option")
    build = service.get("build")
    _require(isinstance(build, dict) and Path(str(build.get("context", ""))).resolve() == root.resolve().parent.parent and str(build.get("dockerfile", "")).replace("\\", "/").endswith("services/retrieval/Dockerfile"), "Resolved retrieval installer build provenance does not match")
    _require(service.get("image") == RETRIEVAL_IMAGE_REF and service.get("profiles") == ["install"], "Resolved retrieval installer identity does not match")
    _require(service.get("entrypoint") == ["python", "-m", "retrieval_worker.install_models"] and service.get("command") == ["--plan"], "Resolved retrieval installer command does not match")
    _require(service.get("environment") == {"HF_HOME": "/models", "HF_HUB_CACHE": "/models/hub", "HF_HUB_OFFLINE": "0", "HF_HUB_DISABLE_TELEMETRY": "1", "TRANSFORMERS_OFFLINE": "0"}, "Resolved retrieval installer environment does not match")
    _require(service.get("read_only") is True and service.get("tmpfs") == ["/tmp:rw,noexec,nosuid,size=64m"], "Resolved retrieval installer filesystem does not match")
    _require(service.get("cap_drop") == ["ALL"] and service.get("security_opt") == ["no-new-privileges:true"], "Resolved retrieval installer privileges do not match")
    user = service.get("user")
    _require(isinstance(user, str) and len(user) <= 24 and user.replace(":", "").isdigit() and user.count(":") == 1, "Resolved retrieval installer user does not match")
    volumes = service.get("volumes")
    _require(
        isinstance(volumes, list)
        and len(volumes) == 1
        and isinstance(volumes[0], dict)
        and volumes[0].get("type", "bind") == "bind"
        and volumes[0].get("target") == "/models"
        and volumes[0].get("read_only", False) is False
        and Path(str(volumes[0].get("source", ""))).is_absolute(),
        "Resolved retrieval installer cache mount does not match",
    )


class RetrievalComposeRunner:
    """A fixed-argument Compose runner for the two independent CPU workers."""

    timeout = 150
    readiness_timeout = 300
    stop_timeout = 75

    def __init__(
        self,
        project_root: Path | None = None,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        *,
        settings: Any | None = None,
        docker_path: str = "docker",
        poll_interval: float = 1.0,
        sleeper: Callable[[float], None] = time.sleep,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.project_root = project_root
        self.settings = settings
        self.run = run
        self.docker_path = docker_path
        self.poll_interval = poll_interval
        self.sleeper = sleeper
        self.monotonic = monotonic_clock

    def _root(self) -> Path:
        root = self.project_root
        if root is None and self.settings is not None:
            loader = getattr(self.settings, "load_retrieval_compose_root", None)
            root = loader() if callable(loader) else None
        if root is None:
            raise RetrievalComposeError("Retrieval Compose project is not configured", "retrieval_project_unconfigured")
        return validate_retrieval_compose_root(Path(root))

    def _compose_args(self, root: Path) -> list[str]:
        return [
            self.docker_path, "compose", "--project-name", RETRIEVAL_PROJECT_NAME,
            "--project-directory", str(root), "--file", str(root / "compose.yaml"),
            "--env-file", str(root / ".env"),
        ]

    def _execute(self, args: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        try:
            result = self.run(args, capture_output=True, text=True, timeout=timeout or self.timeout, shell=False, check=False)
        except subprocess.TimeoutExpired:
            raise RetrievalComposeError("Docker command timed out", "docker_timeout") from None
        except OSError:
            raise RetrievalComposeError("Docker is unavailable", "docker_unavailable") from None
        except subprocess.SubprocessError:
            raise RetrievalComposeError("Docker command failed", "compose_command_failed") from None
        if result.returncode != 0:
            raise RetrievalComposeError("Docker Compose operation was rejected", "compose_command_failed")
        return result

    def validate_project(self) -> Path:
        root = self._root()
        # Include the reviewed installer profile so Compose resolves every service
        # in the contract, while lifecycle commands still target only embed/rerank.
        result = self._execute(
            self._compose_args(root) + ["--profile", "install", "config", "--format", "json"],
            timeout=10,
        )
        try:
            rendered = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError):
            raise RetrievalComposeError("Docker Compose returned invalid resolved configuration", "retrieval_compose_contract_invalid") from None
        _validate_rendered_configuration(root, rendered)
        return root

    def inspect_service(self, service: str) -> dict[str, Any]:
        spec = SERVICE_SPECS.get(service)
        if spec is None:
            raise RetrievalComposeError("Retrieval service is not allowlisted")
        root = self._root()
        result = self._execute(self._compose_args(root) + ["ps", "--all", "--quiet", service], timeout=10)
        ids = [value.strip() for value in result.stdout.splitlines() if value.strip()]
        if not ids:
            return {"service": service, "runtime_state": "absent"}
        if len(ids) != 1 or not _CONTAINER_ID.fullmatch(ids[0]):
            raise RetrievalComposeError("Compose returned an invalid container identity", "compose_identity_mismatch")
        record = self._inspect_container(ids[0], spec)
        state = record.get("State") if isinstance(record.get("State"), dict) else {}
        return {"service": service, "runtime_state": str(state.get("Status", "unknown")), "container_id": ids[0]}

    def _inspect_container(self, container_id: str, spec: dict[str, str]) -> dict[str, Any]:
        if not _CONTAINER_ID.fullmatch(container_id):
            raise RetrievalComposeError("Docker returned an invalid container identity")
        result = self._execute([self.docker_path, "inspect", container_id], timeout=10)
        try:
            records = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError):
            raise RetrievalComposeError("Docker returned invalid container metadata", "runtime_identity_mismatch") from None
        if not isinstance(records, list) or len(records) != 1 or not isinstance(records[0], dict):
            raise RetrievalComposeError("Docker returned an invalid container record", "runtime_identity_mismatch")
        record = records[0]
        config = record.get("Config") if isinstance(record.get("Config"), dict) else {}
        labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
        if labels.get("com.docker.compose.project") != RETRIEVAL_PROJECT_NAME or labels.get("com.docker.compose.service") != spec["service"]:
            raise RetrievalComposeError("Runtime Compose labels do not match the fixed worker", "compose_identity_mismatch")
        if config.get("Image") != RETRIEVAL_IMAGE_REF:
            raise RetrievalComposeError("Runtime retrieval image does not match the pinned image tag", "runtime_identity_mismatch")
        host_config = record.get("HostConfig") if isinstance(record.get("HostConfig"), dict) else {}
        if host_config.get("Privileged") is True or host_config.get("DeviceRequests"):
            raise RetrievalComposeError("Runtime retrieval container has unexpected privileges or GPU allocation", "runtime_privilege_mismatch")
        network_settings = record.get("NetworkSettings") if isinstance(record.get("NetworkSettings"), dict) else {}
        ports = network_settings.get("Ports")
        if isinstance(ports, dict) and any(value for value in ports.values()):
            raise RetrievalComposeError("Runtime retrieval worker publishes a host port", "host_port_published")
        networks = network_settings.get("Networks") if isinstance(network_settings.get("Networks"), dict) else {}
        inference = networks.get(RETRIEVAL_NETWORK_NAME) if isinstance(networks, dict) else None
        aliases = inference.get("Aliases") if isinstance(inference, dict) else None
        if not isinstance(aliases, list) or spec["alias"] not in aliases:
            raise RetrievalComposeError("Runtime retrieval network alias does not match", "network_alias_mismatch")
        mounts = record.get("Mounts")
        cache = [mount for mount in mounts if isinstance(mount, dict) and mount.get("Destination") == "/models" and mount.get("Type") == "bind" and mount.get("RW") is False] if isinstance(mounts, list) else []
        if len(cache) != 1:
            raise RetrievalComposeError("Runtime retrieval model cache is not mounted read-only", "model_cache_mount_mismatch")
        return record

    def _container_id(self, root: Path, service: str) -> str | None:
        result = self._execute(self._compose_args(root) + ["ps", "--all", "--quiet", service], timeout=10)
        ids = [value.strip() for value in result.stdout.splitlines() if value.strip()]
        if not ids:
            return None
        if len(ids) != 1 or not _CONTAINER_ID.fullmatch(ids[0]):
            raise RetrievalComposeError("Compose returned an invalid container identity", "compose_identity_mismatch")
        return ids[0]

    def start(self, service: str, heartbeat: Callable[[], None] | None = None) -> dict[str, Any]:
        return self._operate(service, "start", heartbeat)

    def restart(self, service: str, heartbeat: Callable[[], None] | None = None) -> dict[str, Any]:
        return self._operate(service, "restart", heartbeat)

    def _operate(self, service: str, action: str, heartbeat: Callable[[], None] | None) -> dict[str, Any]:
        if service not in SERVICE_SPECS:
            raise RetrievalComposeError("Retrieval service is not allowlisted")
        root = self.validate_project()
        observed = self.inspect_service(service)
        if observed["runtime_state"] not in {"absent", "created", "restarting", "running", "exited", "dead"}:
            raise RetrievalComposeError("Runtime retrieval worker state is unsupported")
        if observed["runtime_state"] == "absent" and action == "restart":
            raise RetrievalComposeError("Cannot restart a retrieval worker that is not present")
        if observed["runtime_state"] != "absent":
            self._inspect_container(observed["container_id"], SERVICE_SPECS[service])
        if action == "start":
            command = self._compose_args(root) + ["up", "--detach", "--no-deps", service]
        else:
            command = self._compose_args(root) + ["restart", "--timeout", str(self.stop_timeout), service]
        self._execute(command, timeout=self.timeout)
        return self._wait_ready(root, service, heartbeat)

    def stop(self, service: str) -> dict[str, Any]:
        if service not in SERVICE_SPECS:
            raise RetrievalComposeError("Retrieval service is not allowlisted")
        root = self.validate_project()
        observed = self.inspect_service(service)
        if observed["runtime_state"] not in {"absent", "created", "restarting", "running", "exited", "dead"}:
            raise RetrievalComposeError("Runtime retrieval worker state is unsupported")
        if observed["runtime_state"] == "absent":
            return {"service": service, "runtime_state": "absent"}
        self._inspect_container(observed["container_id"], SERVICE_SPECS[service])
        self._execute(self._compose_args(root) + ["stop", "--timeout", str(self.stop_timeout), service], timeout=self.timeout)
        return {"service": service, "runtime_state": "stopped"}

    def _wait_ready(self, root: Path, service: str, heartbeat: Callable[[], None] | None) -> dict[str, Any]:
        deadline = self.monotonic() + self.readiness_timeout
        spec = SERVICE_SPECS[service]
        while self.monotonic() < deadline:
            container_id = self._container_id(root, service)
            if container_id is not None:
                self._inspect_container(container_id, spec)
                result = self._execute([self.docker_path, "exec", container_id, "python", "-c", _PROBE_SCRIPT], timeout=5)
                try:
                    probe = json.loads(result.stdout)
                except (TypeError, json.JSONDecodeError):
                    raise RetrievalComposeError("Retrieval worker returned invalid readiness metadata", "retrieval_operation_failed") from None
                if isinstance(probe, dict) and probe.get("ready_status") == 200:
                    if probe.get("health_status") != 200:
                        raise RetrievalComposeError("Retrieval worker liveness check failed", "liveness_check_failed")
                    if probe.get("models_status") != 200 or probe.get("model_id") != spec["model_id"] or probe.get("revision") != spec["revision"]:
                        raise RetrievalComposeError("Retrieval worker model identity or revision does not match", "model_identity_mismatch")
                    return {"service": service, "runtime_state": "running", "model_id": spec["model_id"], "model_revision": spec["revision"]}
                if isinstance(probe, dict) and probe.get("ready_state") in {"failed", "missing_artifact"}:
                    raise RetrievalComposeError("Retrieval worker model failed to load", "model_load_failed")
            if heartbeat is not None:
                heartbeat()
            self.sleeper(min(self.poll_interval, max(0, deadline - self.monotonic())))
        raise RetrievalComposeError("Retrieval worker readiness timed out", "readiness_timeout")
