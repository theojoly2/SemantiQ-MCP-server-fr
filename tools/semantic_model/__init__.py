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
    add_attribute as _add_attribute_sync,
    add_class as _add_class_sync,
    add_connector as _add_connector_sync,
    get_model as _get_model_file,
    load_full_model,
    upload_model as _upload_model_file,
)


ai_thread_pool = ThreadPoolExecutor(max_workers=4)


def _sanitize(value: str, default: str) -> str:
    v = (value or "").strip()
    v = v.replace("\\", "_").replace("/", "_").replace("\x00", "")
    return v or default


def _model_path(user: str, name: str) -> Path:
    return Path(MODELS_PATH) / _sanitize(user, "default") / f"{_sanitize(name, 'generated')}.json"


def _display_name_from_model(model: dict[str, Any], file_name: str) -> str:
    """Return the human-readable model name stored in the JSON, never the technical file name."""
    json_name = (model or {}).get("name") or ""
    json_name = json_name.strip()
    if json_name:
        return json_name
    if "__" in file_name:
        return file_name.rsplit("__", 1)[0]
    return file_name or "Generated"


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


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


async def touch_model(user: str = "", name: str = "") -> dict[str, Any]:
    """Update the model file modification time without changing its content."""
    import os
    fp = _model_path(user, name)
    if not fp.exists():
        return {"error": f"Model {name} not found"}
    now_ms = _now_ms()
    # os.utime takes seconds; keep millisecond precision for the return value.
    now_s = now_ms / 1000.0
    os.utime(fp, (now_s, now_s))
    return {"ok": True, "last_opened_at": now_ms}


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
                    "last_opened_at": int(stat.st_mtime * 1000),
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
    return {"ok": True, "name": new_name}


async def delete_model(user: str = "", name: str = "") -> dict[str, Any]:
    """Delete a persisted model file."""
    fp = _model_path(user, name)
    if fp.exists():
        fp.unlink()
    return {"ok": True}


async def add_class(title: str, definition: str, usage_note: str, user: str = "", name: str = "", package: str | None = None, uri: str | None = None) -> dict[str, Any]:
    """Add a class to a persisted semantic model."""
    loop = asyncio.get_running_loop()
    model = await get_model(user=user, name=name)
    display_name = _display_name_from_model(model, name)
    func = functools.partial(
        _add_class_sync,
        title=title,
        definition=definition,
        usage_note=usage_note,
        user=user,
        name=display_name,
        package=package,
        uri=uri,
    )
    return await loop.run_in_executor(ai_thread_pool, func)


async def add_attribute(
    class_name: str,
    attr_label: str,
    attr_definition: str,
    attr_uri: str,
    attr_usage_note: str = "",
    attr_type: str | None = "",
    lower_bounds: str = "",
    upper_bounds: str = "",
    user: str = "",
    name: str = "",
) -> dict[str, Any]:
    """Add an attribute to a class in a persisted semantic model."""
    loop = asyncio.get_running_loop()
    model = await get_model(user=user, name=name)
    display_name = _display_name_from_model(model, name)
    func = functools.partial(
        _add_attribute_sync,
        class_name=class_name,
        attr_label=attr_label,
        attr_definition=attr_definition,
        attr_uri=attr_uri,
        attr_usage_note=attr_usage_note,
        attr_type=attr_type,
        lower_bounds=lower_bounds,
        upper_bounds=upper_bounds,
        user=user,
        name=display_name,
    )
    return await loop.run_in_executor(ai_thread_pool, func)


async def add_connector(
    source_name: str,
    target_name: str,
    rel_label: str,
    rel_definition: str,
    rel_uri: str,
    relationship: str,
    lb: str = "",
    rb: str = "",
    lt: str = "",
    rt: str = "",
    rel_usage_note: str = "",
    user: str = "",
    name: str = "",
) -> dict[str, Any]:
    """Add a connector between two classes in a persisted semantic model."""
    loop = asyncio.get_running_loop()
    model = await get_model(user=user, name=name)
    display_name = _display_name_from_model(model, name)
    func = functools.partial(
        _add_connector_sync,
        source_name=source_name,
        target_name=target_name,
        rel_label=rel_label,
        rel_definition=rel_definition,
        rel_uri=rel_uri,
        relationship=relationship,
        lb=lb,
        rb=rb,
        lt=lt,
        rt=rt,
        rel_usage_note=rel_usage_note,
        user=user,
        name=display_name,
    )
    return await loop.run_in_executor(ai_thread_pool, func)


__all__ = [
    "upload_model",
    "get_model",
    "touch_model",
    "list_models",
    "rename_model",
    "delete_model",
    "add_class",
    "add_attribute",
    "add_connector",
]
