from __future__ import annotations

from conftest import AsgiTestClient, load_dashboard_module

PASSWORD = "Pass1234"
LOCAL_ORIGIN = "http://127.0.0.1:8765"


def _client(tmp_path, *, base_url: str = LOCAL_ORIGIN) -> AsgiTestClient:
    api = load_dashboard_module("api")
    app = api.create_app(
        metadata_path=tmp_path / "metadata.sqlite3",
        frontend_dist=tmp_path / "not-built",
    )
    return AsgiTestClient(app, base_url=base_url)


def test_auth_state_and_minimal_liveness_are_public(tmp_path):
    client = _client(tmp_path)

    state = client.get("/auth/state")
    health = client.get("/api/control/v1/controller/health")

    assert state.status_code == 200
    assert state.json() == {"setup_required": True, "authenticated": False}
    assert health.status_code == 200
    assert "inference" not in health.json()
    for path in (
        "/api/control/v1/status",
        "/api/control/v1/telemetry",
        "/api/control/v1/profiles",
        "/auth/sessions",
    ):
        assert client.get(path).status_code == 401


def test_setup_reports_password_policy_and_keeps_token_available(tmp_path):
    client = _client(tmp_path)
    token = client.app.state.auth_service.issue_bootstrap()

    rejected = client.post("/auth/setup", json_body={"token": token, "password": "short"})

    assert rejected.status_code == 422
    assert rejected.json() == {
        "detail": "The password must be 8–256 characters and include at least one uppercase letter, "
        "one lowercase letter, and one digit. Symbols are optional."
    }
    assert client.get("/auth/state").json()["setup_required"] is True

    accepted = client.post("/auth/setup", json_body={"token": token, "password": "Abcd1234"})
    assert accepted.status_code == 204


def test_setup_reports_when_setup_token_is_not_accepted(tmp_path):
    client = _client(tmp_path)

    rejected = client.post(
        "/auth/setup",
        json_body={"token": "not-the-token", "password": PASSWORD},
    )

    assert rejected.status_code == 401
    assert rejected.json() == {
        "detail": "The setup token was not accepted. It may be expired or already used; "
        "copy the current token from setup-token.txt and try again."
    }
    assert client.get("/auth/state").json()["setup_required"] is True


def test_login_sets_rotating_http_only_same_site_cookie(tmp_path):
    client = _client(tmp_path)
    client.setup_admin(PASSWORD)

    first = client.post("/auth/login", json_body={"password": PASSWORD})
    first_cookie = first.cookies.get("dashboard_session")
    second = client.post("/auth/login", json_body={"password": PASSWORD})
    second_cookie = second.cookies.get("dashboard_session")
    set_cookie = second.headers["set-cookie"].casefold()

    assert first.status_code == second.status_code == 200
    assert first_cookie and second_cookie and first_cookie != second_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "secure" not in set_cookie
    assert second.json()["csrf_token"]


def test_secure_cookie_is_set_for_https(tmp_path):
    client = _client(tmp_path, base_url="https://127.0.0.1:8765")
    token = client.app.state.auth_service.issue_bootstrap()
    setup = client.post("/auth/setup", json_body={"token": token, "password": PASSWORD})
    login = client.post("/auth/login", json_body={"password": PASSWORD})

    assert setup.status_code == 204
    assert login.status_code == 200
    assert "secure" in login.headers["set-cookie"].casefold()


def test_wrong_host_or_origin_is_rejected(tmp_path):
    client = _client(tmp_path)
    token = client.app.state.auth_service.issue_bootstrap()
    response = client.post(
        "/auth/setup",
        json_body={"token": token, "password": PASSWORD},
        headers={"host": "attacker.example", "origin": "http://attacker.example"},
    )

    assert response.status_code == 403
    assert client.get("/auth/state").json()["setup_required"] is True


def test_wrong_origin_is_rejected_for_loopback_host(tmp_path):
    client = _client(tmp_path)
    token = client.app.state.auth_service.issue_bootstrap()
    response = client.post(
        "/auth/setup",
        json_body={"token": token, "password": PASSWORD},
        headers={"origin": "http://attacker.example"},
    )

    assert response.status_code == 403
    assert client.get("/auth/state").json()["setup_required"] is True


def test_fastapi_schema_and_debug_docs_are_not_public(tmp_path):
    client = _client(tmp_path)

    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 404


def test_missing_or_wrong_csrf_is_rejected_without_revoking_session(tmp_path):
    client = _client(tmp_path)
    login = client.setup_admin(PASSWORD)
    csrf_token = login.json()["csrf_token"]

    missing = client.post("/auth/logout")
    wrong = client.post("/auth/logout", headers={"X-CSRF-Token": "wrong-token"})

    assert missing.status_code == wrong.status_code == 403
    assert client.get("/api/control/v1/status").status_code == 200
    logout = client.post("/auth/logout", headers={"X-CSRF-Token": csrf_token})
    assert logout.status_code == 204
    assert client.get("/api/control/v1/status").status_code == 401


def test_session_list_and_revoke_others_return_metadata_only(tmp_path):
    client = _client(tmp_path)
    first = client.setup_admin(PASSWORD)
    first_cookie = first.cookies.get("dashboard_session")
    second = client.post("/auth/login", json_body={"password": PASSWORD})
    second_cookie = second.cookies.get("dashboard_session")

    listing = client.get("/auth/sessions")
    serialized = listing.text
    revoked = client.post(
        "/auth/sessions/revoke-others",
        headers={"X-CSRF-Token": second.json()["csrf_token"]},
    )
    remaining = client.get("/auth/sessions")

    assert listing.status_code == 200
    assert len(listing.json()["sessions"]) == 2
    assert first_cookie not in serialized
    assert second_cookie not in serialized
    assert "token_digest" not in serialized
    assert "csrf_digest" not in serialized
    assert revoked.status_code == 200
    assert revoked.json()["revoked"] == 1
    assert len(remaining.json()["sessions"]) == 1
