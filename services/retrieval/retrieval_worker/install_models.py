from __future__ import annotations

import argparse
import os

from .models import (
    EMBEDDING_ALIAS,
    EMBEDDING_MODEL_ID,
    EMBEDDING_REVISION,
    RERANKING_ALIAS,
    RERANKING_CODE_ID,
    RERANKING_CODE_REVISION,
    RERANKING_MODEL_ID,
    RERANKING_REVISION,
)

ARTIFACTS = (
    (EMBEDDING_ALIAS, EMBEDDING_MODEL_ID, EMBEDDING_REVISION),
    (RERANKING_ALIAS, RERANKING_MODEL_ID, RERANKING_REVISION),
    ("gte-reviewed-code", RERANKING_CODE_ID, RERANKING_CODE_REVISION),
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Install only the pinned retrieval artifacts.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--plan", action="store_true", help="Print the pinned artifact plan without writing.")
    action.add_argument("--install", action="store_true", help="Download the pinned artifact into the mounted cache.")
    args = parser.parse_args()

    cache_dir = os.environ.get("HF_HUB_CACHE", "/models/hub")
    for role, model_id, revision in ARTIFACTS:
        print(f"Artifact ({role}): {model_id}@{revision}")
    print("Estimated cache allocation: 4 GiB (conservative planning estimate).")
    if args.plan:
        print("Plan only; no files were written.")
        return

    from huggingface_hub import snapshot_download

    try:
        code_snapshot = None
        for role, model_id, revision in ARTIFACTS:
            snapshot = snapshot_download(
                repo_id=model_id,
                revision=revision,
                cache_dir=cache_dir,
                local_files_only=False,
            )
            if role == "gte-reviewed-code":
                code_snapshot = snapshot
        from .reranking import verify_reviewed_code_snapshot

        if code_snapshot is None:
            raise RuntimeError("Pinned custom model code is missing")
        verify_reviewed_code_snapshot(code_snapshot)
    except Exception:  # noqa: BLE001 - installation errors may include local paths or tokens.
        raise SystemExit("Pinned artifact installation failed.") from None
    print("Pinned retrieval artifacts installed.")


if __name__ == "__main__":
    main()
