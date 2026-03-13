import os
import pytest
from docu_tracker.config import load_config


def test_load_config_defaults(tmp_path, monkeypatch):
    """Should return defaults when no config exists."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config = load_config(config_dir=str(tmp_path))
    assert config["downloads_path"] == os.path.expanduser("~/Downloads")
    assert config["anthropic_api_key"] is None


def test_load_config_from_yaml(tmp_path, monkeypatch):
    """Should load values from config.yaml."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "anthropic_api_key: sk-yaml-key\ndownloads_path: /custom/path\n"
    )
    config = load_config(config_dir=str(tmp_path))
    assert config["anthropic_api_key"] == "sk-yaml-key"
    assert config["downloads_path"] == "/custom/path"


def test_env_var_overrides_yaml(tmp_path, monkeypatch):
    """Env var should take precedence over yaml."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("anthropic_api_key: sk-yaml-key\n")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-key")
    config = load_config(config_dir=str(tmp_path))
    assert config["anthropic_api_key"] == "sk-env-key"


def test_dotenv_loaded(tmp_path, monkeypatch):
    """Should load .env file from project root."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=sk-dotenv-key\n")
    config = load_config(config_dir=str(tmp_path), dotenv_path=str(env_file))
    assert config["anthropic_api_key"] == "sk-dotenv-key"
