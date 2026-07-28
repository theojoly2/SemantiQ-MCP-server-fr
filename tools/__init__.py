import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor

try:
    from .index_search import retrieve_search_documents
    from .index_search import get_available_tags
    from .index_search import get_document_file
    from .index_search import retrieve_document_context
except Exception:
    retrieve_search_documents = None
    get_available_tags = None
    get_document_file = None
    retrieve_document_context = None

from .semantic_model import upload_model
from .semantic_model import get_model
from .semantic_model import touch_model
from .semantic_model import list_models
from .semantic_model import rename_model
from .semantic_model import delete_model
from .semantic_model import add_class as _add_class_sync
from .semantic_model import add_attribute as _add_attribute_sync
from .semantic_model import add_connector as _add_connector_sync
from .semantic_model import upload_model as _upload_model_sync
from .semantic_model import get_model as _get_model_sync
from .semantic_model import touch_model as _touch_model_sync
from .semantic_model import list_models as _list_models_sync
from .semantic_model import rename_model as _rename_model_sync
from .semantic_model import delete_model as _delete_model_sync


# The underlying semantic_model functions are synchronous. fastmcp expects the
# registered tool function itself to be awaitable (or a plain callable, but in
# this version it tries to `await` the tool result). We run the blocking work in
# a thread pool and return a coroutine so the framework can await it safely.
_thread_pool = ThreadPoolExecutor(max_workers=2)


def _run_sync(fn, *args, **kwargs):
    loop = asyncio.get_running_loop()
    return loop.run_in_executor(_thread_pool, functools.partial(fn, *args, **kwargs))


async def _upload_model(model: dict, user: str = "", name: str = "") -> dict:
    return await _run_sync(_upload_model_sync, model=model, user=user, name=name)


async def _get_model(user: str = "", name: str = "") -> dict:
    return await _run_sync(_get_model_sync, user=user, name=name)


async def _touch_model(user: str = "", name: str = "") -> dict:
    return await _run_sync(_touch_model_sync, user=user, name=name)


async def _list_models(user: str = "") -> dict:
    return await _run_sync(_list_models_sync, user=user)


async def _rename_model(user: str = "", old_name: str = "", new_name: str = "") -> dict:
    return await _run_sync(_rename_model_sync, user=user, old_name=old_name, new_name=new_name)


async def _delete_model(user: str = "", name: str = "") -> dict:
    return await _run_sync(_delete_model_sync, user=user, name=name)


async def _add_class(
    title: str,
    definition: str,
    usage_note: str,
    user: str = "",
    name: str = "",
    package: str | None = None,
    uri: str | None = None,
) -> dict:
    return await _run_sync(
        _add_class_sync,
        title=title,
        definition=definition,
        usage_note=usage_note,
        user=user,
        name=name,
        package=package,
        uri=uri,
    )


async def _add_attribute(
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
) -> dict:
    return await _run_sync(
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
        name=name,
    )


async def _add_connector(
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
) -> dict:
    return await _run_sync(
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
        name=name,
    )


__all__ = [
    "retrieve_search_documents",
    "get_available_tags",
    "get_document_file",
    "retrieve_document_context",
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
