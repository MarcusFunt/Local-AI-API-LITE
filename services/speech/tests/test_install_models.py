from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from speech_worker.contracts import (
    STT_MODEL_ID,
    STT_MODEL_REVISION,
    TTS_MODEL_ID,
    TTS_MODEL_REVISION,
    TTS_VOICE_ID,
)
from speech_worker.install_models import build_plan, install_artifacts, main


def test_install_plan_is_pinned_and_does_not_write_model_artifacts(tmp_path):
    plan = build_plan()
    assert plan["stt"] == {"repository": STT_MODEL_ID, "revision": STT_MODEL_REVISION}
    assert plan["tts"] == {
        "repository": TTS_MODEL_ID,
        "revision": TTS_MODEL_REVISION,
        "voice_id": TTS_VOICE_ID,
    }
    main(["--plan"])
    assert not list(tmp_path.iterdir())


def test_installation_downloads_exact_revisions_and_records_voice_hashes(tmp_path):
    stt_snapshot = tmp_path / "hub" / "snapshot" / STT_MODEL_REVISION
    stt_snapshot.mkdir(parents=True)
    (stt_snapshot / "model.bin").write_bytes(b"pinned stt model")
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    voice_source = source_dir / "da_DK-talesyntese-medium.onnx"
    config_source = source_dir / "da_DK-talesyntese-medium.onnx.json"
    voice_source.write_bytes(b"pinned piper voice")
    config_source.write_text('{"audio":{"sample_rate":22050}}', encoding="utf-8")
    calls = []

    def snapshot_download(**kwargs):
        calls.append(("snapshot", kwargs))
        return stt_snapshot

    def hf_hub_download(**kwargs):
        calls.append(("file", kwargs))
        return source_dir / Path(kwargs["filename"]).name

    installed = install_artifacts(
        tmp_path / "models",
        snapshot_download_fn=snapshot_download,
        file_download_fn=hf_hub_download,
    )

    manifest = json.loads(installed["manifest"].read_text(encoding="utf-8"))
    assert all(call[1]["revision"] in {STT_MODEL_REVISION, TTS_MODEL_REVISION} for call in calls)
    assert manifest["repository"] == TTS_MODEL_ID
    assert manifest["revision"] == TTS_MODEL_REVISION
    assert manifest["voice_id"] == TTS_VOICE_ID
    assert manifest["files"]["voice.onnx"] == hashlib.sha256(b"pinned piper voice").hexdigest()
    assert manifest["files"]["voice.onnx.json"] == hashlib.sha256(config_source.read_bytes()).hexdigest()
    assert installed["stt_snapshot"].name == STT_MODEL_REVISION


def test_installer_rejects_a_snapshot_with_a_different_revision(tmp_path):
    wrong_snapshot = tmp_path / "snapshot" / "wrong-commit"
    wrong_snapshot.mkdir(parents=True)
    (wrong_snapshot / "model.bin").write_bytes(b"wrong")

    with pytest.raises(RuntimeError, match="[Pp]inned artifact"):
        install_artifacts(
            tmp_path / "models",
            snapshot_download_fn=lambda **_kwargs: wrong_snapshot,
            file_download_fn=lambda **_kwargs: pytest.fail("voice files must not download after an STT pin mismatch"),
        )
