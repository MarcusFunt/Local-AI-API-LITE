from __future__ import annotations

import argparse
from pathlib import Path

from .configuration import ConfigurationError, LocalSettingsStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Configure the local dashboard controller")
    commands = parser.add_subparsers(dest="command", required=True)
    configure = commands.add_parser(
        "configure-compose-root", help="set the explicitly chosen local inference project"
    )
    configure.add_argument("--path", required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == "configure-compose-root":
        try:
            LocalSettingsStore().configure_compose_root(Path(arguments.path))
        except ConfigurationError as exc:
            parser.error(str(exc))
        print("Local Compose project configured and validated.")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
