from __future__ import annotations

from conftest import load_dashboard_module


def test_trusted_tailscale_origins_only_include_this_dashboard_https_route():
    local_server = load_dashboard_module("local_server")
    identity = {"Self": {"DNSName": "marcus-computer.example.ts.net."}}
    serve = {
        "TCP": {
            "10000": {"HTTPS": True},
            "10001": {"HTTPS": True},
            "10002": {},
        },
        "Web": {
            "marcus-computer.example.ts.net:10000": {
                "Handlers": {"/": {"Proxy": "http://127.0.0.1:8765"}}
            },
            "marcus-computer.example.ts.net:10001": {
                "Handlers": {"/": {"Proxy": "http://127.0.0.1:8766"}}
            },
            "marcus-computer.example.ts.net:10002": {
                "Handlers": {"/": {"Proxy": "http://127.0.0.1:8765"}}
            },
            "other-node.example.ts.net:10000": {
                "Handlers": {"/": {"Proxy": "http://127.0.0.1:8765"}}
            },
        },
    }

    assert local_server.trusted_tailscale_origins(identity, serve) == (
        "https://marcus-computer.example.ts.net:10000",
    )
