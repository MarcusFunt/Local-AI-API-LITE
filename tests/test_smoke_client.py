from __future__ import annotations

import json
import threading
import time
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO

import pytest

from scripts import check_server
from scripts import shared_inference_smoke as smoke


@pytest.fixture
def mock_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            if self.path == "/health":
                with self.server.lock:
                    status = self.server.health_statuses.pop(0) if self.server.health_statuses else self.server.health_status
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path == "/v1/models":
                self.send_response(self.server.models_status)
                self.send_header("Content-Type", "application/json")
                body = json.dumps({"data": [{"id": self.server.model_id}]}).encode()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(404)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            with self.server.lock:
                self.server.request_bodies.append(payload)
                self.server.active += 1
                self.server.max_active = max(self.server.max_active, self.server.active)
                status = self.server.post_statuses.pop(0) if self.server.post_statuses else 200
            if self.server.delay:
                time.sleep(self.server.delay)
            if status != 200:
                body = b'{"error":"private mock response detail"}'
            else:
                content = payload["messages"][0]["content"].lower()
                if "fictional" in content:
                    result = {"speaker": "examiner", "reply": "Please introduce yourself."}
                else:
                    result = {"language": "da", "reply": "Fred."}
                document = {
                    "model": self.server.response_model or payload["model"],
                    "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(result)}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                    "metrics": {
                        "queue_time_ms": 4.25,
                        "time_to_first_token_ms": 38.2,
                        "tokens_per_second": 50.0,
                    },
                }
                body = json.dumps(document).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with self.server.lock:
                    self.server.active -= 1

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.lock = threading.Lock()
    server.active = 0
    server.max_active = 0
    server.request_bodies = []
    server.post_statuses = []
    server.health_status = 200
    server.health_statuses = []
    server.models_status = 200
    server.model_id = "ddu-narrator"
    server.response_model = None
    server.delay = 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_smoke_client_posts_schema_request_and_accepts_valid_history(mock_server):
    server, url = mock_server
    result = smoke.run_case(url, "ddu-narrator", "history", 2, max_retries=0)

    assert result["ok"] is True
    assert result["attempts"] == 1
    assert server.request_bodies[0]["model"] == "ddu-narrator"
    assert server.request_bodies[0]["stream"] is False
    assert server.request_bodies[0]["response_format"]["type"] == "json_schema"
    assert result["completion_tokens"] == 2
    assert result["queue_time_ms"] == 4.25
    assert result["time_to_first_token_ms"] == 38.2
    assert result["tokens_per_second"] == 50


def test_smoke_client_retries_loading_503_and_rate_limit_429(mock_server):
    server, url = mock_server
    server.post_statuses = [503, 429, 200]
    result = smoke.run_case(url, "ddu-narrator", "history", 2, max_retries=2, retry_delay=0)

    assert result["ok"] is True
    assert result["attempts"] == 3
    assert result["retry_count"] == 2


def test_smoke_client_does_not_retry_permanent_http_error_or_print_body(mock_server):
    server, url = mock_server
    server.post_statuses = [400]
    result = smoke.run_case(url, "ddu-narrator", "history", 2, max_retries=3, retry_delay=0)

    assert result["ok"] is False
    assert result["attempts"] == 1
    assert result["exception_class"] == "HTTPError"
    assert "private mock response detail" not in json.dumps(result)


def test_smoke_client_bounds_timeout_and_returns_only_sanitized_fields(mock_server):
    _server, url = mock_server
    _server.delay = 0.3
    started = time.monotonic()
    result = smoke.run_case(url, "ddu-narrator", "history", 0.05, max_retries=0)

    assert result["ok"] is False
    assert result["exception_class"] == "TimeoutError"
    assert result["attempts"] == 1
    assert time.monotonic() - started < 0.25
    assert "Fred." not in json.dumps(result)


def test_check_server_accepts_empty_health_body_and_requires_served_alias(mock_server):
    server, url = mock_server
    assert check_server.check_server(url, "ddu-narrator", timeout=1, max_retries=0)["ok"]

    server.model_id = "unexpected-model"
    with pytest.raises(check_server.ServerCheckError):
        check_server.check_server(url, "ddu-narrator", timeout=1, max_retries=0)


def test_check_server_retries_503_and_429_while_server_is_loading(mock_server):
    server, url = mock_server
    server.health_statuses = [503, 429, 200]

    result = check_server.check_server(url, "ddu-narrator", timeout=1, max_retries=2, retry_delay=0)

    assert result["ok"] is True
    assert result["requests"] == 4


def test_concurrent_cli_path_runs_both_clients_without_logging_prompts_or_outputs(mock_server):
    server, url = mock_server
    server.delay = 0.08
    output = StringIO()

    with redirect_stdout(output):
        exit_code = smoke.main(
            ["--base-url", url, "--model", "ddu-narrator", "--timeout", "2", "--concurrent"]
        )

    result = json.loads(output.getvalue())
    assert exit_code == 0
    assert len(result["results"]) == 2
    assert all(item["ok"] for item in result["results"])
    assert server.max_active == 2
    assert "Versailles" not in output.getvalue()
    assert "Please introduce yourself." not in output.getvalue()
