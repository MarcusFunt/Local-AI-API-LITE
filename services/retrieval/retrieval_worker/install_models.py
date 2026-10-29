from __future__ import annotations

import argparse
import os

from .models import EMBEDDING_ALIAS, EMBEDDING_MODEL_ID, EMBEDDING_REVISION


def main() -> None:
    parser = argparse.ArgumentParser(description="Install only the pinned retrieval artifacts.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--plan", action="store_true", help="Print the pinned artifact plan without writing.")
    action.add_argument("--install", action="store_true", help="Download the pinned artifact into the mounted cache.")
    args = parser.parse_args()

    cache_dir = os.environ.get("HF_HUB_CACHE", "/models/hub")
    print(f"Role: {EMBEDDING_ALIAS}")
    print(f"Artifact: {EMBEDDING_MODEL_ID}@{EMBEDDING_REVISION}")
    print("Estimated cache allocation: 3 GiB (conservative planning estimate).")
    if args.plan:
        print("Plan only; no files were written.")
        return

    from huggingface_hub import snapshot_download

    try:
        snapshot_download(
            repo_id=EMBEDDING_MODEL_ID,
            revision=EMBEDDING_REVISION,
            cache_dir=cache_dir,
            local_files_only=False,
        )
    except Exception:  # noqa: BLE001 - installation errors may include local paths or tokens.
        raise SystemExit("Pinned artifact installation failed.") from None
    print("Pinned embedding artifact installed.")


if __name__ == "__main__":
    main()
