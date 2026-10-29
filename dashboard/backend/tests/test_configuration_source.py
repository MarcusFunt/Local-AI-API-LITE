from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import load_dashboard_module

PROJECT_NAME = "local-ai-api-lite"
IMAGE_REF = "vllm/vllm-openai:v0.32.0@sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34"


def make_project(root: Path, *, name: str = PROJECT_NAME) -> Path:
    root.mkdir(parents=True)
    (root / "compose.yaml").write_text(
        f"""name: {name}
services:
  vllm:
    image: {IMAGE_REF}
    volumes:
      - ${{HF_CACHE_DIR:?set HF_CACHE_DIR}}:/root/.cache/huggingface
    networks:
      inference:
        aliases:
          - ddu-llm
networks:
  inference:
    external: true
    name: ddu_inference_bus
""",
        encoding="utf-8",
    )
    (root / ".env").write_text(
        "MODEL_ID=old/model\nMODEL_REVISION=old-revision\nMODEL_ALIAS=old-alias\n"
        "MAX_MODEL_LEN=1024\nMAX_NUM_SEQS=1\nGPU_MEMORY_UTILIZATION=0.5\n"
        "REASONING_PARSER=old-parser\nHF_CACHE_DIR=/tmp/model-cache\n",
        encoding="utf-8",
    )
    return root


def test_controls_unavailable_without_explicit_root(tmp_path):
    configuration = load_dashboard_module("configuration")
    settings = configuration.LocalSettingsStore(tmp_path / "settings.json")

    assert settings.load_compose_root() is None
    assert settings.controls_available() is False


def test_settings_command_requires_and_uses_an_explicit_root(tmp_path, monkeypatch, capsys):
    settings_command = load_dashboard_module("settings")
    selected: list[Path] = []

    class FakeSettingsStore:
        def configure_compose_root(self, path):
            selected.append(path)

    monkeypatch.setattr(settings_command, "LocalSettingsStore", FakeSettingsStore)
    with pytest.raises(SystemExit):
        settings_command.main(["configure-compose-root"])
    assert settings_command.main(
        ["configure-compose-root", "--path", str(tmp_path / "chosen-project")]
    ) == 0

    assert selected == [tmp_path / "chosen-project"]
    assert "configured and validated" in capsys.readouterr().out


def test_project_root_must_be_existing_expected_compose_project(tmp_path):
    configuration = load_dashboard_module("configuration")
    settings = configuration.LocalSettingsStore(tmp_path / "settings.json")
    not_a_project = tmp_path / "missing"
    wrong_project = make_project(tmp_path / "wrong-project", name="unrelated-project")

    with pytest.raises(configuration.ConfigurationError):
        settings.configure_compose_root(not_a_project)
    with pytest.raises(configuration.ConfigurationError):
        settings.configure_compose_root(wrong_project)
    assert settings.load_compose_root() is None


def test_explicit_project_is_saved_only_after_configuration_validation(tmp_path):
    configuration = load_dashboard_module("configuration")
    settings = configuration.LocalSettingsStore(tmp_path / "private" / "settings.json")
    root = make_project(tmp_path / "project")
    validated: list[Path] = []

    settings.configure_compose_root(root, validate_configuration=lambda path: validated.append(path))

    assert validated == [root.resolve()]
    assert settings.load_compose_root() == root.resolve()
    if os.name != "nt":
        assert settings.path.stat().st_mode & 0o777 == 0o600


def test_project_root_is_not_saved_when_compose_validation_fails(tmp_path):
    configuration = load_dashboard_module("configuration")
    settings = configuration.LocalSettingsStore(tmp_path / "private" / "settings.json")
    root = make_project(tmp_path / "project")

    def reject(_path):
        raise configuration.ConfigurationError("Compose validation failed")

    with pytest.raises(configuration.ConfigurationError):
        settings.configure_compose_root(root, validate_configuration=reject)

    assert settings.load_compose_root() is None
    assert not settings.path.exists()


def test_profile_values_update_only_allowlisted_env_keys_atomically(tmp_path):
    configuration = load_dashboard_module("configuration")
    root = make_project(tmp_path / "project")
    env_path = root / ".env"
    before = env_path.read_text(encoding="utf-8")
    validated_text: list[str] = []

    def validate(candidate_env: Path) -> None:
        assert candidate_env != env_path
        assert candidate_env.exists()
        validated_text.append(candidate_env.read_text(encoding="utf-8"))

    configuration.update_profile_environment(
        root,
        "baseline-8k-4",
        validate_configuration=validate,
    )

    updated = env_path.read_text(encoding="utf-8")
    assert "MAX_MODEL_LEN=8192" in updated
    assert "MAX_NUM_SEQS=4" in updated
    assert "MODEL_ID=RedHatAI/Qwen3.5-4B-quantized.w4a16" in updated
    assert "MODEL_REVISION=7a613872f394578b0b52b683ff4ac47516b4bcaf" in updated
    assert "MAX_MODEL_LEN=8192" in validated_text[0]
    assert "HF_CACHE_DIR=/tmp/model-cache" in updated
    assert "old-alias" not in updated
    assert before != updated
    if os.name != "nt":
        assert env_path.stat().st_mode & 0o777 == 0o600
    assert list(root.glob(".env.*.tmp")) == []


def test_env_update_preserves_comments_and_unrelated_secret_values(tmp_path):
    configuration = load_dashboard_module("configuration")
    root = make_project(tmp_path / "project")
    env_path = root / ".env"
    unrelated = "MONITOR_DATABASE_PASSWORD=synthetic-unrelated-secret\n"
    comment = "# Keep this operator note exactly as written\n"
    original = env_path.read_text(encoding="utf-8").replace(
        "MAX_MODEL_LEN=1024", "MAX_MODEL_LEN=1024  # Preserve this inline note"
    )
    env_path.write_text(comment + original + unrelated, encoding="utf-8")

    configuration.update_profile_environment(root, "baseline-4k-2", validate_configuration=lambda _path: None)

    updated = env_path.read_text(encoding="utf-8")
    assert updated.startswith(comment)
    assert unrelated in updated
    assert "MAX_MODEL_LEN=4096  # Preserve this inline note" in updated
    assert "MAX_MODEL_LEN=4096" in updated
    assert "MONITOR_DATABASE_PASSWORD" in updated


def test_unknown_or_modified_profile_is_rejected_before_env_update(tmp_path, monkeypatch):
    configuration = load_dashboard_module("configuration")
    root = make_project(tmp_path / "project")
    env_path = root / ".env"
    original = env_path.read_text(encoding="utf-8")
    validator = lambda _path: pytest.fail("untrusted profile reached validation")
    current_profiles = configuration.load_profiles()
    modified_profiles = [dict(profile) for profile in current_profiles]
    modified_profiles[0]["max_model_len"] = 1
    monkeypatch.setattr(configuration, "load_profiles", lambda: modified_profiles)

    with pytest.raises(configuration.ConfigurationError):
        configuration.update_profile_environment(root, "experimental-unvalidated", validator)
    with pytest.raises(configuration.ConfigurationError):
        configuration.update_profile_environment(
            root,
            "baseline-4k-2",
            validator,
        )
    assert env_path.read_text(encoding="utf-8") == original
