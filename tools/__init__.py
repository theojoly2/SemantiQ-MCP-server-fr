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
from .semantic_model import add_class
from .semantic_model import add_attribute
from .semantic_model import add_connector


def _identity(fn):
    return fn


# Wrap async semantic-model tools so server.py can register them as plain tools
# even when running in an environment without qdrant_client (search tools are None).
# The wrappers are transparent at runtime; they preserve the original signatures.
@_identity
async def _upload_model(model: dict, user: str = "", name: str = "") -> dict:
    return await upload_model(model=model, user=user, name=name)


@_identity
async def _get_model(user: str = "", name: str = "") -> dict:
    return await get_model(user=user, name=name)


@_identity
async def _touch_model(user: str = "", name: str = "") -> dict:
    return await touch_model(user=user, name=name)


@_identity
async def _list_models(user: str = "") -> dict:
    return await list_models(user=user)


@_identity
async def _rename_model(user: str = "", old_name: str = "", new_name: str = "") -> dict:
    return await rename_model(user=user, old_name=old_name, new_name=new_name)


@_identity
async def _delete_model(user: str = "", name: str = "") -> dict:
    return await delete_model(user=user, name=name)


@_identity
async def _add_class(
    title: str,
    definition: str,
    usage_note: str,
    user: str = "",
    name: str = "",
    package: str | None = None,
    uri: str | None = None,
) -> dict:
    return await add_class(
        title=title,
        definition=definition,
        usage_note=usage_note,
        user=user,
        name=name,
        package=package,
        uri=uri,
    )


@_identity
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
    return await add_attribute(
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


@_identity
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
    return await add_connector(
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
