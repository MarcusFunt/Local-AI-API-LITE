from __future__ import annotations

import importlib
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

import psutil


def _optional_number(nvml: Any, function_name: str, *args: Any) -> float | None:
    function = getattr(nvml, function_name, None)
    if function is None:
        return None
    try:
        value = function(*args)
    except Exception:  # noqa: BLE001 - NVML exposes vendor-specific errors for unsupported sensors.
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def read_gpu_telemetry(
    nvml_loader: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Read device-wide GPU telemetry without allocating GPU memory."""
    loader = nvml_loader or importlib.import_module
    initialized = False
    nvml = None
    try:
        nvml = loader("pynvml")
        nvml.nvmlInit()
        initialized = True
        device = nvml.nvmlDeviceGetHandleByIndex(0)
        memory = nvml.nvmlDeviceGetMemoryInfo(device)
        utilization = nvml.nvmlDeviceGetUtilizationRates(device)
        temperature = _optional_number(
            nvml,
            "nvmlDeviceGetTemperature",
            device,
            getattr(nvml, "NVML_TEMPERATURE_GPU", 0),
        )
        milliwatts = _optional_number(nvml, "nvmlDeviceGetPowerUsage", device)
        return {
            "available": True,
            "scope": "device-wide",
            "reason": None,
            "utilization_percent": _safe_percent(getattr(utilization, "gpu", None)),
            "memory_used_bytes": _safe_nonnegative_int(getattr(memory, "used", None)),
            "memory_total_bytes": _safe_nonnegative_int(getattr(memory, "total", None)),
            "memory_free_bytes": _safe_nonnegative_int(getattr(memory, "free", None)),
            "temperature_c": _safe_nonnegative_number(temperature),
            "power_watts": (
                round(milliwatts / 1000, 2) if milliwatts is not None and milliwatts >= 0 else None
            ),
        }
    except Exception as exc:  # noqa: BLE001 - any NVML failure must become unavailable telemetry.
        return {
            "available": False,
            "scope": "device-wide",
            "reason": f"NVML unavailable ({type(exc).__name__})",
            "utilization_percent": None,
            "memory_used_bytes": None,
            "memory_total_bytes": None,
            "memory_free_bytes": None,
            "temperature_c": None,
            "power_watts": None,
        }
    finally:
        if initialized and nvml is not None:
            with suppress(Exception):
                nvml.nvmlShutdown()


def _safe_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return int(value)


def _safe_nonnegative_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return round(float(value), 1)


def _safe_percent(value: Any) -> float | None:
    number = _safe_nonnegative_number(value)
    return number if number is not None and number <= 100 else None


def read_host_telemetry(cache_path: str | Path | None = None) -> dict[str, Any]:
    """Sample host metrics; missing sensors and paths remain explicitly unavailable."""
    try:
        cpu_percent = psutil.cpu_percent(interval=0.1)
    except (psutil.Error, OSError, RuntimeError, ValueError, TypeError, AttributeError):
        cpu_percent = None
    try:
        memory = psutil.virtual_memory()
        ram_total = _safe_nonnegative_int(memory.total)
        ram_available = _safe_nonnegative_int(memory.available)
        ram_used = max(0, ram_total - ram_available) if ram_total is not None and ram_available is not None else None
    except (psutil.Error, OSError, RuntimeError, ValueError, TypeError, AttributeError):
        ram_total = None
        ram_used = None

    disk_free: int | None = None
    if cache_path:
        try:
            disk_free = _safe_nonnegative_int(psutil.disk_usage(str(cache_path)).free)
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError):
            disk_free = None

    return {
        "cpu_percent": _safe_percent(cpu_percent),
        "ram_used_bytes": ram_used,
        "ram_total_bytes": ram_total,
        "cache_disk_free_bytes": disk_free,
        "gpu": read_gpu_telemetry(),
    }
