# Local dashboard

The dashboard is a local React/Vite UI and a small FastAPI controller. It provides authenticated status, inference lifecycle controls, bounded jobs and history, sanitized service logs, and optional read-only PostgreSQL monitoring. It binds only to loopback. Start, stop, restart, and profile actions require a stable Compose project path in local settings and an explicit confirmation.

See the [operator runbook](../docs/DASHBOARD.md) for Windows installation, administrator setup and recovery, runtime paths, security boundaries, and the manual host acceptance checklist.

See the [security review](../docs/DASHBOARD_SECURITY.md) and [acceptance record](../docs/DASHBOARD_ACCEPTANCE.md) for the current boundaries, automated evidence, and pending host checks.

## Requirements

- Windows 10/11 with Python 3.11 or newer.
- Node.js 22.12 or newer.
- Docker Desktop running in the same Windows user session when inference status is needed.
- Optional NVIDIA driver/NVML for device-wide GPU metrics.

## Install for the current Windows user

From the repository root, run the installer in the same Windows account that uses Docker Desktop:

```powershell
.\dashboard\scripts\install_dashboard.ps1
```

The installer builds the frontend, creates a private Python environment, secures the user-scoped data directory, and registers and starts the `Local-AI-API-LITE Dashboard Controller` Scheduled Task for the current session and future interactive sign-ins. On a first install it prints a one-time setup token to that local console. Use it at <http://127.0.0.1:8765> to choose the administrator password. The task binds only to loopback and does not run inference Compose.

Remove the current-user startup task with:

```powershell
.\dashboard\scripts\uninstall_dashboard.ps1
```

Uninstall removes the dashboard task and its running controller process. It preserves local metadata, the DPAPI-protected vault key, and all inference containers, volumes, and networks.

## Run manually

The same controller can be started in a terminal without installing the Scheduled Task:

```powershell
Push-Location .\dashboard\backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[gpu]"
Push-Location ..\frontend
npm ci
npm run build
Pop-Location
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

The CLI and controller commands must run from `dashboard/backend` so Python can import the `app` package. The production frontend is served from `dashboard/frontend/dist`.
