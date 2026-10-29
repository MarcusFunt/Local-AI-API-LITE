from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from app.paths import data_directory, ensure_private_directory


def _windows_acl(path: Path) -> tuple[bool, set[str]]:
    result = subprocess.run(
        ["icacls.exe", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    whoami = subprocess.run(["whoami.exe"], check=True, capture_output=True, text=True)
    acl = result.stdout.casefold()
    current_identity = whoami.stdout.strip().casefold()
    identities = {
        line.split(":", 1)[0].casefold()
        for line in result.stdout.splitlines()
        if ":" in line
    }
    is_protected = "(I)" not in result.stdout.replace("(OI)", "").replace("(CI)", "")
    assert current_identity in acl
    return is_protected, identities


def test_data_directory_uses_local_app_data_on_windows(tmp_path, monkeypatch):
    local_app_data = tmp_path / "Local App Data"
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setattr("app.paths.platform.system", lambda: "Windows")

    assert data_directory() == local_app_data / "Local-AI-API-LITE" / "Dashboard"


def test_runtime_data_is_outside_repository(tmp_path, monkeypatch):
    runtime_root = tmp_path / "private-runtime"
    monkeypatch.setenv("LOCALAPPDATA", str(runtime_root))
    monkeypatch.setattr("app.paths.platform.system", lambda: "Windows")

    runtime_path = data_directory().resolve()
    repository_root = Path(__file__).resolve().parents[3]

    assert runtime_root.resolve() in runtime_path.parents
    assert repository_root.resolve() not in runtime_path.parents


def test_runtime_directory_is_private_to_current_user(tmp_path):
    private_path = tmp_path / "dashboard-data"
    ensure_private_directory(private_path)

    assert private_path.is_dir()
    if os.name == "nt":
        protected, identities = _windows_acl(private_path)
        assert protected
        assert len(identities) == 1
        assert not {"everyone", "nt authority\\authenticated users", "builtin\\users"} & identities
    else:
        assert stat.S_IMODE(private_path.stat().st_mode) == 0o700
