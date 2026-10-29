from __future__ import annotations

from pathlib import Path

from conftest import AsgiTestClient

from app.api import create_app


class FakeSettingsStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def load_compose_root(self) -> Path:
        return self.root


def make_job(job_id: str, *, profile_id: str, gpu_model: str, prompt: str | None = None) -> dict:
    summary = {
        "ok": True,
        "profile_id": profile_id,
        "model_revision": "a" * 40,
        "engine": "vLLM 0.32.0",
        "engine_image_digest": f"sha256:{'a' * 64}",
        "context_length": 4096 if profile_id.endswith("4k-2") else 8192,
        "max_num_seqs": 2 if profile_id.endswith("4k-2") else 4,
        "host": {"platform": "Windows", "architecture": "AMD64"},
        "hardware": {"gpu_model": gpu_model},
        "script_version": "1",
        "request_count": 10,
        "success_count": 10,
        "error_count": 0,
        "latency_percentiles": {"p50_seconds": 1.2, "p95_seconds": 2.3},
    }
    if prompt:
        summary["prompt"] = prompt
    return {
        "job_id": job_id,
        "kind": "performance-benchmark",
        "state": "completed",
        "created_at": "2026-10-29T12:00:00Z",
        "started_at": "2026-10-29T12:00:01Z",
        "completed_at": "2026-10-29T12:00:10Z",
        "options": {"profile_id": profile_id, "concurrency": 1},
        "summary": summary,
        "error_category": None,
        "artifact_id": f"{job_id}.jsonl",
        "exit_code": 0,
    }


class StubJobRunner:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.records = [
            make_job("a" * 32, profile_id="baseline-4k-2", gpu_model="NVIDIA RTX 3060", prompt="SECRET_PROMPT"),
            make_job("b" * 32, profile_id="baseline-8k-4", gpu_model="NVIDIA RTX 4090"),
        ]

    def start(self, job_id, options):
        self.calls.append(("start", job_id, options))
        record = {
            "job_id": "c" * 32,
            "kind": job_id,
            "state": "queued",
            "created_at": "2026-10-29T12:00:00Z",
            "started_at": None,
            "completed_at": None,
            "options": options,
            "summary": {},
            "error_category": None,
            "artifact_id": None,
            "exit_code": None,
        }
        self.records.append(record)
        return record

    def cancel(self, job_id):
        self.calls.append(("cancel", job_id))
        return True

    def status(self, job_id):
        self.calls.append(("status", job_id))
        return next((record for record in self.records if record["job_id"] == job_id), None)

    def history(self, limit, offset):
        self.calls.append(("history", limit, offset))
        return self.records[:150]

    def preview_cleanup(self):
        return {"removable_jobs": 2, "removable_artifacts": 1}

    def cleanup_history(self, *, confirm):
        self.calls.append(("cleanup", confirm))
        return {"deleted_jobs": 2, "deleted_artifacts": 1}


def make_client(tmp_path, runner=None):
    stub = runner or StubJobRunner()
    app = create_app(
        metadata_path=tmp_path / "metadata.sqlite3",
        frontend_dist=tmp_path / "missing-dist",
        settings_store=FakeSettingsStore(tmp_path),
        operation_coordinator=object(),
        job_runner=stub,
    )
    return AsgiTestClient(app), stub


def test_job_routes_require_auth_and_csrf(tmp_path):
    client, runner = make_client(tmp_path)

    assert client.get("/api/control/v1/jobs/history").status_code == 401
    assert client.post(
        "/api/control/v1/jobs/start", json_body={"job_id": "server-readiness", "options": {}}
    ).status_code == 401

    client.setup_admin()
    assert client.get("/api/control/v1/jobs/history").status_code == 200
    start = client.post(
        "/api/control/v1/jobs/start", json_body={"job_id": "server-readiness", "options": {}}
    )
    assert start.status_code == 403
    start = client.post(
        "/api/control/v1/jobs/start",
        json_body={"job_id": "server-readiness", "options": {}},
        headers={"X-CSRF-Token": client.csrf_token},
    )
    assert start.status_code == 202
    job_id = start.json()["job_id"]

    assert client.post(f"/api/control/v1/jobs/{job_id}/cancel").status_code == 403
    cancel = client.post(
        f"/api/control/v1/jobs/{job_id}/cancel",
        headers={"X-CSRF-Token": client.csrf_token},
    )
    assert cancel.status_code == 200
    assert ("cancel", job_id) in runner.calls


def test_history_is_bounded_and_sanitized(tmp_path):
    client, runner = make_client(tmp_path)
    client.setup_admin()

    response = client.get("/api/control/v1/jobs/history?limit=1&offset=0")

    assert response.status_code == 200
    assert len(response.json()["jobs"]) == 1
    assert runner.calls[-1] == ("history", 1, 0)
    assert "SECRET_PROMPT" not in response.text
    assert "prompt" not in response.text
    assert response.json()["jobs"][0]["summary"]["hardware"]["gpu_model"] == "NVIDIA RTX 3060"
    assert client.get("/api/control/v1/jobs/history?limit=1000").status_code == 422


def test_only_compatible_runs_compare_as_comparable(tmp_path):
    client, _runner = make_client(tmp_path)
    client.setup_admin()

    different = client.get(
        f"/api/control/v1/jobs/compare?left_id={'a' * 32}&right_id={'b' * 32}"
    ).json()
    same = make_job("d" * 32, profile_id="baseline-4k-2", gpu_model="NVIDIA RTX 3060")
    client.app.state.job_runner.records.append(same)
    compatible = client.get(
        f"/api/control/v1/jobs/compare?left_id={'a' * 32}&right_id={'d' * 32}"
    ).json()

    assert different["comparable"] is False
    assert {item["field"] for item in different["differences"]} >= {"profile_id", "hardware.gpu_model"}
    assert compatible["comparable"] is True


def test_cleanup_requires_a_dry_run_and_explicit_confirmation(tmp_path):
    client, runner = make_client(tmp_path)
    client.setup_admin()

    preview = client.get("/api/control/v1/jobs/cleanup/preview")
    denied = client.post(
        "/api/control/v1/jobs/cleanup",
        json_body={"confirm": False},
        headers={"X-CSRF-Token": client.csrf_token},
    )
    deleted = client.post(
        "/api/control/v1/jobs/cleanup",
        json_body={"confirm": True},
        headers={"X-CSRF-Token": client.csrf_token},
    )

    assert preview.json() == {"removable_jobs": 2, "removable_artifacts": 1}
    assert denied.status_code == 409
    assert deleted.status_code == 200
    assert runner.calls[-1] == ("cleanup", True)
