import os
import yaml
from dotenv import load_dotenv


def load_config(config_dir=None, dotenv_path=None):
    if dotenv_path:
        load_dotenv(dotenv_path)

    if config_dir is None:
        config_dir = os.path.expanduser("~/.docu-tracker")

    config = {
        "downloads_path": os.path.expanduser("~/Downloads"),
        "scan_paths": [os.path.expanduser("~/Downloads")],
        "anthropic_api_key": None,
        "model": "claude-haiku-4-5-20251001",
    }

    yaml_path = os.path.join(config_dir, "config.yaml")
    if os.path.exists(yaml_path):
        with open(yaml_path) as f:
            yaml_config = yaml.safe_load(f) or {}
        if "downloads_path" in yaml_config:
            config["downloads_path"] = yaml_config["downloads_path"]
        if "scan_paths" in yaml_config:
            paths = yaml_config["scan_paths"]
            config["scan_paths"] = [os.path.expanduser(p) for p in paths]
        if "anthropic_api_key" in yaml_config:
            config["anthropic_api_key"] = yaml_config["anthropic_api_key"]
        if "model" in yaml_config:
            config["model"] = yaml_config["model"]

    env_key = os.environ.get("ANTHROPIC_API_KEY")
    if env_key:
        config["anthropic_api_key"] = env_key
    env_model = os.environ.get("DOCU_TRACKER_MODEL")
    if env_model:
        config["model"] = env_model

    return config
