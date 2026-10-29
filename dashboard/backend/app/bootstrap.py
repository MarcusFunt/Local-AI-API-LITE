from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from app.auth import AuthenticationError, AuthService
from app.paths import data_directory
from app.persistence import MetadataStore, MetadataStoreError


def _service() -> AuthService:
    return AuthService(MetadataStore(data_directory() / "metadata.sqlite3"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage local dashboard administrator tokens")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("issue-token", help="issue a one-time initial setup token")
    commands.add_parser("recover-admin", help="revoke sessions and issue a one-time recovery token")
    args = parser.parse_args(argv)

    try:
        service = _service()
        if args.command == "issue-token":
            if not service.is_setup_required():
                print("Dashboard administrator is already configured; no setup token was issued.", file=sys.stderr)
                return 2
            token = service.issue_bootstrap()
        else:
            token = service.issue_recovery()
    except AuthenticationError:
        print("Dashboard administrator token could not be issued.", file=sys.stderr)
        return 2
    except (MetadataStoreError, OSError):
        print("Dashboard runtime storage is unavailable.", file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 - never print runtime details or token material to logs.
        print("Dashboard administrator token could not be issued.", file=sys.stderr)
        return 1

    # The raw token exists only in this process and the local console stream.
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
