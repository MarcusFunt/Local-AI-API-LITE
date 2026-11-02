from __future__ import annotations

import json
import os
import shutil
import subprocess
from urllib.parse import urlsplit

DASHBOARD_PROXY = "http://127.0.0.1:8765"


def trusted_tailscale_origins(identity: object, serve: object) -> tuple[str, ...]:
    if not isinstance(identity, dict) or not isinstance(serve, dict):
        return ()
    self_info = identity.get("Self")
    if not isinstance(self_info, dict):
        return ()
    dns_name = self_info.get("DNSName")
    if not isinstance(dns_name, str):
        return ()
    dns_name = dns_name.rstrip(".").casefold()
    if not dns_name.endswith(".ts.net"):
        return ()

    tcp = serve.get("TCP")
    web = serve.get("Web")
    if not isinstance(tcp, dict) or not isinstance(web, dict):
        return ()

    origins: set[str] = set()
    for address, service in web.items():
        if not isinstance(address, str) or not isinstance(service, dict):
            continue
        try:
            parsed = urlsplit(f"//{address}")
            port = parsed.port
        except ValueError:
            continue
        if (
            parsed.hostname is None
            or parsed.hostname.casefold() != dns_name
            or port is None
            or parsed.username
            or parsed.password
        ):
            continue
        tcp_service = tcp.get(str(port))
        if not isinstance(tcp_service, dict) or tcp_service.get("HTTPS") is not True:
            continue
        handlers = service.get("Handlers")
        if not isinstance(handlers, dict):
            continue
        root_handler = handlers.get("/")
        if isinstance(root_handler, dict) and root_handler.get("Proxy") == DASHBOARD_PROXY:
            origins.add(f"https://{dns_name}:{port}")
    return tuple(sorted(origins))


def discover_tailscale_origins() -> tuple[str, ...]:
    executable = shutil.which("tailscale.exe") or shutil.which("tailscale")
    if executable is None:
        return ()
    try:
        identity_result = subprocess.run(
            [executable, "status", "--json"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        serve_result = subprocess.run(
            [executable, "serve", "status", "--json"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        return trusted_tailscale_origins(
            json.loads(identity_result.stdout),
            json.loads(serve_result.stdout),
        )
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return ()


def main() -> None:
    trusted_origins = discover_tailscale_origins()
    if trusted_origins:
        os.environ["DASHBOARD_TRUSTED_ORIGINS"] = ",".join(trusted_origins)
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8765,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
    )


if __name__ == "__main__":
    main()
