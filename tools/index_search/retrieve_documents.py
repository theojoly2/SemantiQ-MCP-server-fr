"""Compatibility wrapper around retrieve_search_documents for the Assistant tools.

In autre_version the tool exposed to the LLM was named `retrieve_documents` with the
signature (search_terms, limit=10, return_full_document=True). The current server
already has a richer `retrieve_search_documents` implementation; this module simply
adapts its output to the legacy tuple format.
"""

from typing import List, Tuple, Any

try:
    from .retrieve_search_documents import retrieve_search_documents
except ImportError:
    from retrieve_search_documents import retrieve_search_documents


def retrieve_documents(
    search_terms: str,
    limit: int = 10,
    return_full_document: bool = True,
) -> List[Tuple[str, str, float]]:
    """Return retrieved documents as (filename, text, score) tuples.

    Args:
        search_terms: Query text (bilingual short sentences preferred).
        limit: Maximum number of documents to return.
        return_full_document: When True the full reconstructed document is used
            as the returned text; otherwise only the best chunk is returned.
            (Currently the underlying search always returns the best chunk,
            this flag only influences which text field is picked.)

    Returns:
        List of tuples (filename, text, score).
    """
    results = retrieve_search_documents(search_terms, tags=None, limit=limit)
    output: List[Tuple[str, str, float]] = []
    for row in results:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        filename = str(row[0] if row[0] is not None else "")
        best_chunk = str(row[1] if row[1] is not None else "")
        summary = str(row[2] if len(row) > 2 and row[2] is not None else "")
        score = float(row[4] if len(row) > 4 and row[4] is not None else 0.0)
        text = summary if return_full_document and summary else best_chunk
        output.append((filename, text, score))
    return output
