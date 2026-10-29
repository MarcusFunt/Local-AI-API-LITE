from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "dashboard" / "backend"
APP = BACKEND / "app"

sys.path.insert(0, str(BACKEND))


def load_dashboard_module(name: str):
    module_path = APP / f"{name}.py"
    assert module_path.is_file(), f"dashboard app module {name!r} must be implemented"
    return importlib.import_module(f"app.{name}")


class AsgiTestClient:
    def __init__(self, app, base_url: str = "http://127.0.0.1:8765"):
        self.app = app
        self.base_url = base_url.rstrip("/")
        self.cookies = httpx.Cookies()
        self.csrf_token: str | None = None

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body=None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        async def send() -> httpx.Response:
            transport = httpx.ASGITransport(app=self.app)
            request_headers = {"Origin": self.base_url}
            for name, value in (headers or {}).items():
                for existing in tuple(request_headers):
                    if existing.casefold() == name.casefold():
                        del request_headers[existing]
                request_headers[name] = value
            async with httpx.AsyncClient(
                transport=transport,
                base_url=self.base_url,
                cookies=self.cookies,
            ) as client:
                response = await client.request(
                    method,
                    path,
                    json=json_body,
                    headers=request_headers,
                )
                self.cookies = client.cookies
                return response

        return asyncio.run(send())

    def get(self, path: str) -> httpx.Response:
        return self.request("GET", path)

    def post(self, path: str, *, json_body=None, headers: dict[str, str] | None = None) -> httpx.Response:
        return self.request("POST", path, json_body=json_body, headers=headers)

    def setup_admin(self, password: str = "correct horse battery staple") -> httpx.Response:
        token = self.app.state.auth_service.issue_bootstrap()
        setup = self.post("/auth/setup", json_body={"token": token, "password": password})
        assert setup.status_code == 204
        login = self.post("/auth/login", json_body={"password": password})
        assert login.status_code == 200
        self.csrf_token = login.json()["csrf_token"]
        return login


@pytest.fixture
def dashboard_root() -> Path:
    return ROOT
