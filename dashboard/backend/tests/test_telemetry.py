from __future__ import annotations

from conftest import load_dashboard_module


def test_missing_nvml_returns_unavailable_metrics_instead_of_zero():
    telemetry = load_dashboard_module("telemetry")

    def missing_nvml(_name: str):
        raise ModuleNotFoundError("pynvml is not installed")

    result = telemetry.read_gpu_telemetry(nvml_loader=missing_nvml)

    assert result["available"] is False
    assert result["utilization_percent"] is None
    assert result["memory_used_bytes"] is None
    assert result["memory_total_bytes"] is None


def test_nvml_reports_device_wide_metrics_and_keeps_unsupported_fields_unavailable():
    telemetry = load_dashboard_module("telemetry")

    class FakeNvml:
        NVML_TEMPERATURE_GPU = 0

        def nvmlInit(self):
            pass

        def nvmlShutdown(self):
            pass

        def nvmlDeviceGetHandleByIndex(self, index):
            assert index == 0
            return "gpu0"

        def nvmlDeviceGetName(self, device):
            assert device == "gpu0"
            return b"NVIDIA GeForce RTX 3060"

        def nvmlDeviceGetMemoryInfo(self, device):
            assert device == "gpu0"
            return type("Memory", (), {"used": 1024, "total": 4096, "free": 3072})()

        def nvmlDeviceGetUtilizationRates(self, device):
            assert device == "gpu0"
            return type("Utilization", (), {"gpu": 7})()

    result = telemetry.read_gpu_telemetry(nvml_loader=lambda _name: FakeNvml())

    assert result["available"] is True
    assert result["scope"] == "device-wide"
    assert result["device_name"] == "NVIDIA GeForce RTX 3060"
    assert result["utilization_percent"] == 7
    assert result["memory_used_bytes"] == 1024
    assert result["memory_total_bytes"] == 4096
    assert result["temperature_c"] is None
    assert result["power_watts"] is None


def test_host_telemetry_keeps_gpu_unavailable_and_reports_cpu_ram_and_disk(monkeypatch, tmp_path):
    telemetry = load_dashboard_module("telemetry")

    monkeypatch.setattr(telemetry.psutil, "cpu_percent", lambda interval=None: 12.5)
    monkeypatch.setattr(
        telemetry.psutil,
        "virtual_memory",
        lambda: type("Memory", (), {"total": 1000, "available": 400})(),
    )
    monkeypatch.setattr(
        telemetry.psutil,
        "disk_usage",
        lambda _path: type("Disk", (), {"total": 2000, "free": 900})(),
    )
    monkeypatch.setattr(
        telemetry,
        "read_gpu_telemetry",
        lambda: {
            "available": False,
            "reason": "NVML unavailable",
            "utilization_percent": None,
            "memory_used_bytes": None,
            "memory_total_bytes": None,
            "temperature_c": None,
            "power_watts": None,
        },
    )

    result = telemetry.read_host_telemetry(cache_path=tmp_path)

    assert result["cpu_percent"] == 12.5
    assert result["ram_used_bytes"] == 600
    assert result["ram_total_bytes"] == 1000
    assert result["cache_disk_free_bytes"] == 900
    assert result["gpu"]["available"] is False
    assert result["gpu"]["utilization_percent"] is None
