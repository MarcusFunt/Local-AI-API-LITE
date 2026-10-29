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
    def __init__(self, app):
        self.app = app

    def request(self, method: str, path: str) -> httpx.Response:
        async def send() -> httpx.Response:
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.request(method, path)

        return asyncio.run(send())

    def get(self, path: str) -> httpx.Response:
        return self.request("GET", path)

    def post(self, path: str) -> httpx.Response:
        return self.request("POST", path)


@pytest.fixture
def dashboard_root() -> Path:
    return ROOT
