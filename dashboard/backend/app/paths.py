from __future__ import annotations

import csv
import os
import platform
import subprocess
from pathlib import Path


class RuntimePathError(RuntimeError):
    """Raised when the dashboard cannot secure its runtime directory."""


def data_directory() -> Path:
    """Return the per-user dashboard data directory without creating it."""
    if platform.system() == "Windows":
        root = os.environ.get("LOCALAPPDATA")
        base = Path(root) if root else Path.home() / "AppData" / "Local"
    else:
        root = os.environ.get("XDG_DATA_HOME")
        base = Path(root) if root else Path.home() / ".local" / "share"
    return base / "Local-AI-API-LITE" / "Dashboard"


def ensure_private_directory(path: Path) -> None:
    """Create a directory readable and writable only by the current user."""
    resolved = path.expanduser().resolve()
    try:
        resolved.mkdir(parents=True, exist_ok=True)
        if platform.system() == "Windows":
            _restrict_windows_acl(resolved)
        else:
            resolved.chmod(0o700)
    except (OSError, subprocess.SubprocessError, RuntimePathError):
        raise RuntimePathError("Cannot secure dashboard runtime directory") from None


def _restrict_windows_acl(path: Path) -> None:
    identity = subprocess.run(
        ["whoami.exe", "/user", "/fo", "csv", "/nh"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    row = next(csv.reader(identity.stdout.splitlines()), [])
    sid = next((value for value in row if value.startswith("S-1-")), None)
    if sid is None:
        raise RuntimePathError("Cannot identify current Windows user")

    subprocess.run(
        ["icacls.exe", str(path), "/reset", "/T", "/C"],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    subprocess.run(
        ["icacls.exe", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F"],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
