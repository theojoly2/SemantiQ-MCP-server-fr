"""RAG retrieval tool for the assistant.

Mirrors autre_version's retrieve_documents: it runs a hybrid search via
retrieve_search_documents and then reconstructs either the full document
(for short documents) or a local window around the best matching chunk
(for long documents).  The returned 3-tuple (filename, text, score) is the
format expected by the LLM tools.
"""

from typing import List, Tuple, Any

from qdrant_client.models import Filter, FieldCondition, MatchValue

try:
    from .retrieve_search_documents import retrieve_search_documents
except ImportError:
    from retrieve_search_documents import retrieve_search_documents

try:
    from .load_documents import config as cf
except ImportError:
    from load_documents import config as cf


client = cf.client
COLLECTION = cf.COLLECTION

WINDOW_RADIUS: int = int(
    getattr(cf, "config", {}).get("search", {}).get("window_radius", 4)
    if hasattr(cf, "config")
    else 4
)
FULL_DOCUMENT_CHUNK_THRESHOLD: int = int(
    getattr(cf, "config", {}).get("search", {}).get("full_document_chunk_threshold", 12)
    if hasattr(cf, "config")
    else 12
)
SCROLL_LIMIT_PER_DOC: int = int(
    getattr(cf, "config", {}).get("search", {}).get("scroll_limit_per_doc", 10000)
    if hasattr(cf, "config")
    else 10000
)


def _load_document_chunks(document_id: str) -> list[Any]:
    """Return every chunk of a document ordered by chunk_index."""
    all_points = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION,
            scroll_filter=Filter(
                must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]
            ),
            limit=SCROLL_LIMIT_PER_DOC,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        if not points:
            break
        all_points.extend(points)
        if offset is None:
            break
    return sorted(all_points, key=lambda p: int((p.payload or {}).get("chunk_index", 0)))


def _build_partial_header(
    filename: str,
    best_chunk_index: int,
    start_idx: int,
    end_idx: int,
    total_chunks: int,
) -> str:
    return (
        "[PARTIAL DOCUMENT ONLY]\n"
        f"filename={filename}\n"
        f"best_chunk_index={best_chunk_index}\n"
        f"returned_chunk_range={start_idx}-{end_idx}\n"
        f"total_chunks={total_chunks}\n"
        "note=Only a local window around the best matching chunk is returned, not the full document.\n\n"
    )


def _load_best_view_for_document(
    document_id: str,
    best_chunk_index: int,
    filename: str,
    return_full_document: bool = True,
    is_single_doc: bool = False,
) -> str:
    ordered = _load_document_chunks(document_id)
    if not ordered:
        return ""

    total_chunks = len(ordered)

    if not return_full_document:
        for p in ordered:
            payload = p.payload or {}
            if int(payload.get("chunk_index", 0)) == best_chunk_index:
                return str(payload.get("text", ""))
        return ""

    if total_chunks <= FULL_DOCUMENT_CHUNK_THRESHOLD:
        return "\n".join(
            str((p.payload or {}).get("text", ""))
            for p in ordered
            if (p.payload or {}).get("text")
        ).strip()

    eff_radius = WINDOW_RADIUS * 3 if is_single_doc else WINDOW_RADIUS
    start_idx = best_chunk_index - eff_radius
    end_idx = best_chunk_index + eff_radius

    if start_idx < 0:
        missing_start = 0 - start_idx
        end_idx += missing_start
        start_idx = 0
    if end_idx >= total_chunks:
        missing_end = end_idx - (total_chunks - 1)
        start_idx -= missing_end
        end_idx = total_chunks - 1
    if start_idx < 0:
        start_idx = 0

    selected = [
        p for p in ordered
        if start_idx <= int((p.payload or {}).get("chunk_index", 0)) <= end_idx
    ]
    partial_text = "\n".join(
        str((p.payload or {}).get("text", ""))
        for p in selected
        if (p.payload or {}).get("text")
    ).strip()

    return _build_partial_header(
        filename=filename,
        best_chunk_index=best_chunk_index,
        start_idx=start_idx,
        end_idx=end_idx,
        total_chunks=total_chunks,
    ) + partial_text


def retrieve_documents(
    search_terms: str,
    tags: list = None,
    limit: int = 10,
    return_full_document: bool = True,
) -> List[Tuple[str, str, float]]:
    """Return retrieved documents as (filename, text, score) tuples.

    Args:
        search_terms: Query text (bilingual short sentences preferred).
        tags: Optional list of source tags to filter results.
        limit: Maximum number of documents to return.
        return_full_document: When True the full reconstructed document (or a
            window around the best chunk for long documents) is used as the
            returned text; otherwise only the best chunk is returned.

    Returns:
        List of tuples (filename, text, score).
    """
    results = retrieve_search_documents(search_terms, tags=tags, limit=limit)
    output: List[Tuple[str, str, float]] = []
    is_single_doc = (limit == 1)
    for row in results:
        if not isinstance(row, (list, tuple)) or len(row) < 8:
            continue
        filename = str(row[0] if row[0] is not None else "")
        best_chunk_index = int(row[7] if row[7] is not None else 0)
        document_id = str(row[6] if row[6] is not None else "")
        score = float(row[4] if row[4] is not None else 0.0)
        text = _load_best_view_for_document(
            document_id=document_id,
            best_chunk_index=best_chunk_index,
            filename=filename,
            return_full_document=return_full_document,
            is_single_doc=is_single_doc,
        )
        output.append((filename, text, score))
    return output
