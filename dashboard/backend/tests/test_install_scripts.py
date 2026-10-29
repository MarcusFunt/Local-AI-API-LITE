from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

from app.paths import ensure_private_directory

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
INSTALL_SCRIPT = REPOSITORY_ROOT / "dashboard" / "scripts" / "install_dashboard.ps1"
UNINSTALL_SCRIPT = REPOSITORY_ROOT / "dashboard" / "scripts" / "uninstall_dashboard.ps1"
TASK_NAME = "Local-AI-API-LITE Dashboard Controller"


def test_install_script_registers_interactive_logon_restart_task():
    script = INSTALL_SCRIPT.read_text(encoding="utf-8")

    assert f"$TaskName = '{TASK_NAME}'" in script
    assert "New-ScheduledTaskTrigger -AtLogOn" in script
    assert "-LogonType Interactive" in script
    assert "-RunLevel Limited" in script
    assert "-RestartCount 3" in script
    assert "Register-ScheduledTask" in script
    assert "Start-ScheduledTask -TaskName $TaskName" in script
    assert "22.12.0" in script
    assert "3.11.0" in script


def test_install_script_binds_only_loopback_and_does_not_start_inference():
    script = INSTALL_SCRIPT.read_text(encoding="utf-8")

    assert "--host 127.0.0.1 --port 8765" in script
    assert "0.0.0.0" not in script
    assert "docker compose" not in script.casefold()
    assert "compose up" not in script.casefold()
    assert "app.bootstrap issue-token" in script


def test_uninstall_script_removes_task_but_preserves_runtime_data():
    script = UNINSTALL_SCRIPT.read_text(encoding="utf-8")

    assert f"$TaskName = '{TASK_NAME}'" in script
    assert "Unregister-ScheduledTask" in script
    assert "Stop-ScheduledTask" in script
    assert "Remove-Item -Recurse" not in script
    assert "Local-AI-API-LITE\\Dashboard" not in script
    assert "root-key.dpapi" not in script


def test_install_and_uninstall_resolve_task_identity_by_sid():
    install_script = INSTALL_SCRIPT.read_text(encoding="utf-8")
    uninstall_script = UNINSTALL_SCRIPT.read_text(encoding="utf-8")

    for script in (install_script, uninstall_script):
        assert ". (Join-Path $PSScriptRoot 'task_identity.ps1')" in script
        assert "Test-TaskPrincipalForCurrentUser" in script
        assert "Principal.UserId, $CurrentUser" not in script


def test_task_identity_helper_accepts_unqualified_current_username(tmp_path):
    if os.name != "nt":
        import pytest

        pytest.skip("Windows account translation is only available on Windows")

    powershell = shutil.which("powershell.exe")
    if powershell is None:
        import pytest

        pytest.skip("Windows PowerShell is unavailable")

    helper = REPOSITORY_ROOT / "dashboard" / "scripts" / "task_identity.ps1"
    current_user = subprocess.run(
        ["whoami.exe"], check=True, capture_output=True, text=True
    ).stdout.strip()
    unqualified_user = current_user.rsplit("\\", 1)[-1].replace("'", "''")
    test_script = tmp_path / "check_task_identity.ps1"
    test_script.write_text(
        f". '{helper}'; if (-not (Test-TaskPrincipalForCurrentUser -TaskUserId '{unqualified_user}')) {{ exit 1 }}\n",
        encoding="utf-8-sig",
    )

    subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(test_script),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_runtime_acl_is_user_scoped(tmp_path):
    runtime = tmp_path / "dashboard-runtime"
    ensure_private_directory(runtime)

    if os.name == "nt":
        acl = subprocess.run(
            ["icacls.exe", str(runtime)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        identity = subprocess.run(["whoami.exe"], check=True, capture_output=True, text=True).stdout.strip()
        grants = [line for line in acl.splitlines() if ":" in line]
        assert identity.casefold() in acl.casefold()
        assert len(grants) == 1
        assert "(I)" not in acl.replace("(OI)", "").replace("(CI)", "")
        assert not any(name in acl.casefold() for name in ("everyone", "authenticated users", "builtin\\users"))
    else:
        assert stat.S_IMODE(runtime.stat().st_mode) == 0o700
