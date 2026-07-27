"""Semantic model tools exported for the MCP server.

Reuses the existing tool file copied from autre_version and adds wrappers
for the fastmcp server.
"""

import asyncio
import functools
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from config import config
from resources.semantic_model.utils import MODELS_PATH
from tools.semantic_model.semantic_model import (
    get_model as _get_model_file,
    load_full_model,
    upload_model as _upload_model_file,
)


ai_thread_pool = ThreadPoolExecutor(max_workers=2)


def _sanitize(value: str, default: str) -> str:
    v = (value or "").strip()
    v = v.replace("\\", "_").replace("/", "_").replace("\x00", "")
    return v or default


def _model_path(user: str, name: str) -> Path:
    return Path(MODELS_PATH) / _sanitize(user, "default") / f"{_sanitize(name, 'generated')}.json"


async def upload_model(model: dict[str, Any], user: str = "", name: str = "") -> dict[str, Any]:
    """Persist a semantic model as JSON under resources/semantic_model/models/{user}/{name}.json."""
    loop = asyncio.get_running_loop()
    func = functools.partial(_upload_model_file, model=model, user=user, name=name)
    return await loop.run_in_executor(ai_thread_pool, func)


async def get_model(user: str = "", name: str = "") -> dict[str, Any]:
    """Return the persisted model JSON for the given user and name."""
    loop = asyncio.get_running_loop()
    func = functools.partial(_get_model_file, user=user, name=name)
    return await loop.run_in_executor(ai_thread_pool, func)


async def touch_model(user: str = "", name: str = "") -> dict[str, Any]:
    """Update the model file modification time without changing its content."""
    import os
    import time
    fp = _model_path(user, name)
    if not fp.exists():
        return {"error": f"Model {name} not found"}
    now = time.time()
    os.utime(fp, (now, now))
    return {"ok": True, "last_opened_at": int(now)}


async def list_models(user: str = "") -> dict[str, Any]:
    """List all persisted model names for a user, with last opened/access time."""
    import os
    user_dir = Path(MODELS_PATH) / _sanitize(user, "default")
    models = []
    if user_dir.exists():
        for fp in user_dir.glob("*.json"):
            try:
                data = load_full_model(user=user, name=fp.stem)
                stat = os.stat(fp)
                models.append({
                    "name": fp.stem,
                    "source_format": data.get("source_format", ""),
                    "last_opened_at": int(stat.st_mtime),
                })
            except Exception:
                models.append({"name": fp.stem, "source_format": "", "last_opened_at": 0})
    # Most recently opened first
    models.sort(key=lambda m: m.get("last_opened_at", 0), reverse=True)
    return {"models": models}


async def rename_model(user: str = "", old_name: str = "", new_name: str = "") -> dict[str, Any]:
    """Rename a persisted model file."""
    old_fp = _model_path(user, old_name)
    if not old_fp.exists():
        return {"error": f"Model {old_name} not found"}
    new_fp = _model_path(user, new_name)
    if new_fp.exists():
        return {"error": f"Model {new_name} already exists"}
    old_fp.rename(new_fp)
    # update embedded name key if present
    try:
        model = load_full_model(user=user, name=new_name)
        model["name"] = new_name
        loop = asyncio.get_running_loop()
        func = functools.partial(_upload_model_file, model=model, user=user, name=new_name)
        await loop.run_in_executor(ai_thread_pool, func)
    except Exception:
        pass
    return {"ok": True, "name": new_name}


async def delete_model(user: str = "", name: str = "") -> dict[str, Any]:
    """Delete a persisted model file."""
    fp = _model_path(user, name)
    if fp.exists():
        fp.unlink()
    return {"ok": True}


__all__ = [
    "upload_model",
    "get_model",
    "touch_model",
    "list_models",
    "rename_model",
    "delete_model",
]
