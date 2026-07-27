"""Model persistence helpers for the semantic model MCP tools."""

import json
import re
from pathlib import Path
from typing import Any

from config import config


MODELS_PATH = Path(config["dir_paths"]["models"])
MODELS_PATH.mkdir(parents=True, exist_ok=True)


def _sanitize_path_component(value: str | None, default: str) -> str:
    value = (value or "").strip()
    value = value.replace("\\", "_").replace("/", "_").replace("\x00", "")
    return value.strip() or default


def _model_path(user: str, name: str) -> Path:
    safe_user = _sanitize_path_component(user, "default")
    safe_name = _sanitize_path_component(name, "generated")
    user_dir = MODELS_PATH / safe_user
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / f"{safe_name}.json"


def save_model(user: str, name: str, model: dict[str, Any]) -> dict[str, Any]:
    fp = _model_path(user, name)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, indent=2)
    # Read back to return canonical data
    with open(fp, "r", encoding="utf-8") as f:
        return json.load(f)


def load_model(user: str, name: str) -> dict[str, Any]:
    fp = _model_path(user, name)
    if not fp.exists():
        return {}
    with open(fp, "r", encoding="utf-8") as f:
        raw = f.read().strip()
        if not raw:
            return {}
        return json.loads(raw)


def list_models(user: str) -> list[dict[str, Any]]:
    user_dir = MODELS_PATH / _sanitize_path_component(user, "default")
    if not user_dir.exists():
        return []
    models = []
    for fp in sorted(user_dir.glob("*.json")):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
            models.append({
                "name": fp.stem,
                "source_format": data.get("source_format", ""),
            })
        except Exception:
            continue
    return models


def delete_model(user: str, name: str) -> bool:
    fp = _model_path(user, name)
    if fp.exists():
        fp.unlink()
        return True
    return False


def rename_model(user: str, old_name: str, new_name: str) -> dict[str, Any]:
    old_fp = _model_path(user, old_name)
    if not old_fp.exists():
        raise FileNotFoundError(f"Model {old_name} not found for user {user}")
    new_fp = _model_path(user, new_name)
    if new_fp.exists():
        raise FileExistsError(f"Model {new_name} already exists for user {user}")
    old_fp.rename(new_fp)
    model = load_model(user, new_name)
    model["name"] = new_name
    return save_model(user, new_name, model)
