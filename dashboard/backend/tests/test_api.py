from __future__ import annotations

from conftest import AsgiTestClient, load_dashboard_module


class HealthyDocker:
    def read(self):
        return {
            "availability": "available",
            "inference_state": "Ready",
            "container": {"state": "running", "health": "healthy", "cpu_percent": 1.0},
            "configuration": {
                "model_id": "RedHatAI/Qwen3.5-4B-quantized.w4a16",
                "model_revision": "7a613872f394578b0b52b683ff4ac47516b4bcaf",
                "model_alias": "ddu-narrator",
                "reasoning_parser": "qwen3",
                "max_model_len": 8192,
                "max_num_seqs": 4,
                "gpu_memory_utilization": 0.75,
                "engine": "vLLM 0.32.0",
                "quantization": "W4A16",
            },
            "active_profile": "baseline-8k-4",
            "last_health_probe_at": "2026-10-28T19:40:18Z",
            "last_failure_reason": None,
            "cache_path": "C:/Users/operator/private-cache",
        }


def _host_metrics(cache_path=None):
    return {
        "cpu_percent": 12.5,
        "ram_used_bytes": 600,
        "ram_total_bytes": 1000,
        "cache_disk_free_bytes": None,
        "gpu": {
            "available": False,
            "reason": "NVML unavailable",
            "utilization_percent": None,
            "memory_used_bytes": None,
            "memory_total_bytes": None,
            "temperature_c": None,
            "power_watts": None,
        },
    }


def test_controller_health_is_available_when_inference_is_not(tmp_path):
    api = load_dashboard_module("api")

    class BrokenDocker:
        def read(self):
            raise RuntimeError("Docker unavailable")

    client = AsgiTestClient(
        api.create_app(
            docker_reader=BrokenDocker(),
            telemetry_reader=_host_metrics,
            metadata_path=tmp_path / "metadata.sqlite3",
        )
    )

    response = client.get("/api/control/v1/controller/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_status_exposes_read_only_runtime_profile_and_no_control_routes(dashboard_root, tmp_path):
    api = load_dashboard_module("api")
    client = AsgiTestClient(
        api.create_app(
            docker_reader=HealthyDocker(),
            telemetry_reader=_host_metrics,
            profiles_path=dashboard_root / "dashboard" / "config" / "profiles.yaml",
            metadata_path=tmp_path / "metadata.sqlite3",
        )
    )
    client.setup_admin()

    response = client.get("/api/control/v1/status")

    assert response.status_code == 200
    assert response.json()["inference"]["state"] == "Ready"
    assert response.json()["active_profile"]["id"] == "baseline-8k-4"
    assert response.json()["inference"]["configuration"]["reasoning_parser"] == "qwen3"
    assert response.json()["telemetry"]["gpu"]["utilization_percent"] is None
    assert "C:/Users/operator/private-cache" not in response.text
    assert "cache_path" not in response.json()
    assert client.post("/api/control/v1/inference/stop").status_code in {404, 405}


def test_profiles_endpoint_only_lists_validated_read_only_profiles(dashboard_root, tmp_path):
    api = load_dashboard_module("api")
    client = AsgiTestClient(
        api.create_app(
            docker_reader=HealthyDocker(),
            telemetry_reader=_host_metrics,
            profiles_path=dashboard_root / "dashboard" / "config" / "profiles.yaml",
            metadata_path=tmp_path / "metadata.sqlite3",
        )
    )
    client.setup_admin()

    response = client.get("/api/control/v1/profiles")

    assert response.status_code == 200
    assert [profile["id"] for profile in response.json()["profiles"]] == [
        "baseline-4k-2",
        "baseline-8k-4",
    ]
    assert response.json()["profiles"][1]["reasoning_parser"] == "qwen3"


def test_controller_serves_a_frontend_static_fixture(dashboard_root, tmp_path):
    api = load_dashboard_module("api")
    frontend_dist = tmp_path / "frontend-dist"
    frontend_dist.mkdir()
    (frontend_dist / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    client = AsgiTestClient(
        api.create_app(
            docker_reader=HealthyDocker(),
            telemetry_reader=_host_metrics,
            profiles_path=dashboard_root / "dashboard" / "config" / "profiles.yaml",
            frontend_dist=frontend_dist,
            metadata_path=tmp_path / "metadata.sqlite3",
        )
    )

    response = client.get("/")

    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text


def test_invalid_profile_registry_returns_a_safe_unavailable_response(dashboard_root, tmp_path):
    api = load_dashboard_module("api")
    registry = tmp_path / "profiles.yaml"
    registry.write_text("version: 1\nprofiles: {}\n", encoding="utf-8")
    client = AsgiTestClient(
        api.create_app(
            docker_reader=HealthyDocker(),
            telemetry_reader=_host_metrics,
            profiles_path=registry,
            frontend_dist=dashboard_root / "dashboard" / "frontend" / "dist",
            metadata_path=tmp_path / "metadata.sqlite3",
        )
    )
    client.setup_admin()

    response = client.get("/api/control/v1/profiles")

    assert response.status_code == 503
    assert response.json() == {
        "profiles": [],
        "message": "The dashboard profile registry is unavailable",
    }
