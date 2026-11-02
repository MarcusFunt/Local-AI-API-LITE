from __future__ import annotations

import hashlib
import json

import pytest
from speech_worker.install_models import build_plan, install_artifacts, main

STT_REVISION = "536b0662742c02347bc0e980a01041f333bce120"
TTS_REVISION = "375a0fe641dea077c2a47b4e9a056d6da521eed3"
VOICE_FILES = {
    "da": {
        "voice.onnx": "da/da_DK/talesyntese/medium/da_DK-talesyntese-medium.onnx",
        "voice.onnx.json": "da/da_DK/talesyntese/medium/da_DK-talesyntese-medium.onnx.json",
    },
    "en": {
        "voice.onnx": "en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx",
        "voice.onnx.json": "en/en_US/ljspeech/medium/en_US-ljspeech-medium.onnx.json",
    },
}


@pytest.fixture
def downloads(tmp_path):
    snapshot = tmp_path / "hub" / "snapshot" / STT_REVISION
    snapshot.mkdir(parents=True)
    (snapshot / "model.bin").write_bytes(b"pinned stt model")
    sources = {}
    for language, files in VOICE_FILES.items():
        for local_name, remote_name in files.items():
            source = tmp_path / "source" / remote_name
            source.parent.mkdir(parents=True, exist_ok=True)
            content = language.encode() + b" pinned piper voice"
            if local_name == "voice.onnx.json":
                content = b'{"audio":{"sample_rate":22050}}'
            source.write_bytes(content)
            sources[remote_name] = source
    calls = []

    def snapshot_download(**kwargs):
        calls.append(("snapshot", kwargs))
        return snapshot

    def file_download(**kwargs):
        calls.append(("file", kwargs))
        return sources[kwargs["filename"]]

    return snapshot_download, file_download, calls, sources


def test_install_plan_is_pinned_and_does_not_write_model_artifacts(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MODEL_ROOT", str(tmp_path))
    assert build_plan() == {
        "stt": {"repository": "Systran/faster-whisper-small", "revision": STT_REVISION},
        "tts": {
            "repository": "rhasspy/piper-voices",
            "revision": TTS_REVISION,
            "voices": {
                "da": {"voice_id": "da_DK-talesyntese-medium", "files": VOICE_FILES["da"]},
                "en": {"voice_id": "en_US-ljspeech-medium", "files": VOICE_FILES["en"]},
            },
        },
    }
    main(["--plan"])
    output = capsys.readouterr().out
    assert TTS_REVISION in output
    assert "da_DK-talesyntese-medium" in output
    assert "en_US-ljspeech-medium" in output
    assert "no files were written" in output
    assert not list(tmp_path.iterdir())


def test_installation_downloads_exact_revisions_and_records_voice_hashes(tmp_path, downloads):
    snapshot_download, file_download, calls, sources = downloads
    model_root = tmp_path / "models"
    voice_dir = model_root / "speech" / "piper"
    voice_dir.mkdir(parents=True)
    unrelated = voice_dir / "operator-notes.txt"
    unrelated.write_text("keep this", encoding="utf-8")
    installed = install_artifacts(
        model_root,
        snapshot_download_fn=snapshot_download,
        file_download_fn=file_download,
    )

    cache_dir = str(model_root / "hub")
    assert calls == [
        ("snapshot", {"repo_id": "Systran/faster-whisper-small", "revision": STT_REVISION,
                      "cache_dir": cache_dir, "local_files_only": False}),
        *[("file", {"repo_id": "rhasspy/piper-voices", "filename": remote_name,
                    "revision": TTS_REVISION, "cache_dir": cache_dir, "local_files_only": False})
          for files in VOICE_FILES.values() for remote_name in files.values()],
    ]
    manifest = json.loads(installed["manifest"].read_text(encoding="utf-8"))
    expected_voices = {}
    for language, voice_id in [("da", "da_DK-talesyntese-medium"), ("en", "en_US-ljspeech-medium")]:
        hashes = {}
        for local_name, remote_name in VOICE_FILES[language].items():
            source_bytes = sources[remote_name].read_bytes()
            assert (voice_dir / language / local_name).read_bytes() == source_bytes
            hashes[local_name] = hashlib.sha256(source_bytes).hexdigest()
        expected_voices[language] = {"voice_id": voice_id, "files": hashes}
    assert manifest == {"repository": "rhasspy/piper-voices", "revision": TTS_REVISION,
                        "voices": expected_voices}
    assert installed == {"stt_snapshot": tmp_path / "hub" / "snapshot" / STT_REVISION,
                         "voice_dir": voice_dir, "manifest": voice_dir / "manifest.json"}
    assert unrelated.read_text(encoding="utf-8") == "keep this"
    assert not (voice_dir / "manifest.json.tmp").exists()


@pytest.mark.parametrize("language", ["da", "en"])
@pytest.mark.parametrize("damage", [
    "missing_model", "empty_model", "missing_config", "invalid_json", "config_list",
    "audio_list", "sample_rate_bool", "sample_rate_zero", "sample_rate_negative", "sample_rate_string",
])
def test_installer_rejects_missing_or_invalid_voice_file(tmp_path, downloads, language, damage):
    snapshot_download, file_download, _calls, sources = downloads
    local_name = "voice.onnx" if damage.endswith("model") else "voice.onnx.json"
    source = sources[VOICE_FILES[language][local_name]]
    if damage.startswith("missing_"):
        source.unlink()
    elif damage == "empty_model":
        source.write_bytes(b"")
    else:
        invalid_configs = {
            "invalid_json": "{", "config_list": "[]", "audio_list": '{"audio":[]}',
            "sample_rate_bool": '{"audio":{"sample_rate":true}}',
            "sample_rate_zero": '{"audio":{"sample_rate":0}}',
            "sample_rate_negative": '{"audio":{"sample_rate":-1}}',
            "sample_rate_string": '{"audio":{"sample_rate":"22050"}}',
        }
        source.write_text(invalid_configs[damage], encoding="utf-8")
    model_root = tmp_path / "models"
    voice_dir = model_root / "speech" / "piper"
    voice_dir.mkdir(parents=True)
    manifest_path = voice_dir / "manifest.json"
    manifest_path.write_text("previous manifest", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Pinned artifact installation failed"):
        install_artifacts(model_root, snapshot_download_fn=snapshot_download, file_download_fn=file_download)
    assert manifest_path.read_text(encoding="utf-8") == "previous manifest"
    assert not (voice_dir / "manifest.json.tmp").exists()


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
