from typing import List, Any, Tuple, Dict

from qdrant_client.models import (
    SparseVector,
    Prefetch,
    FusionQuery,
    Fusion,
    Filter,
    FieldCondition,
    MatchValue,
)

try:
    from .load_documents import config as cf
except ImportError:
    from load_documents import config as cf


client = cf.client
model = cf.model
COLLECTION = cf.COLLECTION

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"

SEARCH_LIMIT: int = int(
    getattr(cf, "config", {}).get("search", {}).get("limit", 3)
    if hasattr(cf, "config")
    else 3
)

CANDIDATE_MULTIPLIER: int = int(
    getattr(cf, "config", {}).get("search", {}).get("candidate_multiplier", 8)
    if hasattr(cf, "config")
    else 8
)

MIN_CANDIDATES: int = int(
    getattr(cf, "config", {}).get("search", {}).get("min_candidates", 30)
    if hasattr(cf, "config")
    else 30
)

SCROLL_LIMIT_PER_DOC: int = int(
    getattr(cf, "config", {}).get("search", {}).get("scroll_limit_per_doc", 10000)
    if hasattr(cf, "config")
    else 10000
)


def detect_model_capabilities(model) -> dict[str, Any]:
    """
    Detect if the loaded model supports dense vectors, sparse vectors, or both.
    """
    capabilities = {
        "has_dense": False,
        "has_sparse": False,
        "dense_dim": None,
    }

    test_text = "test"

    try:
        result = model.encode(
            [test_text],
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,
        )

        if isinstance(result, dict):
            dense_vecs = result.get("dense_vecs")
            lexical_weights = result.get("lexical_weights")

            if dense_vecs is not None and len(dense_vecs) > 0:
                capabilities["has_dense"] = True
                capabilities["dense_dim"] = len(dense_vecs[0])

            if lexical_weights is not None:
                capabilities["has_sparse"] = True

            return capabilities

    except Exception:
        pass

    try:
        dense = model.encode([test_text])
        first_dense = dense[0] if hasattr(dense, "__len__") else dense
        capabilities["has_dense"] = True
        capabilities["dense_dim"] = len(first_dense)
        return capabilities
    except Exception as e:
        raise ValueError(f"Could not detect model capabilities: {e}")


def _lexical_to_sparse_vector(lexical_weights: dict[Any, Any]) -> SparseVector:
    return SparseVector(
        indices=[int(k) for k in lexical_weights.keys()],
        values=[float(v) for v in lexical_weights.values()],
    )


def encode_query(query_text: str, capabilities: dict[str, Any]) -> dict[str, Any]:
    """
    Encode a query into dense and/or sparse vectors depending on model capabilities.
    """
    outputs: dict[str, Any] = {}

    if capabilities["has_dense"] and capabilities["has_sparse"]:
        result = model.encode(
            [query_text],
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,
        )

        dense_vecs = result.get("dense_vecs", [])
        lexical_weights = result.get("lexical_weights", [])

        if len(dense_vecs) > 0:
            dense = dense_vecs[0]
            outputs["dense"] = dense.tolist() if hasattr(dense, "tolist") else list(dense)

        if len(lexical_weights) > 0:
            outputs["sparse"] = _lexical_to_sparse_vector(lexical_weights[0])

        return outputs

    if capabilities["has_dense"]:
        dense = model.encode([query_text])
        first_dense = dense[0] if hasattr(dense, "__len__") else dense
        outputs["dense"] = (
            first_dense.tolist()
            if hasattr(first_dense, "tolist")
            else list(first_dense)
        )
        return outputs

    if capabilities["has_sparse"]:
        result = model.encode(
            [query_text],
            return_dense=False,
            return_sparse=True,
            return_colbert_vecs=False,
        )
        lexical_weights = result.get("lexical_weights", [])
        if len(lexical_weights) > 0:
            outputs["sparse"] = _lexical_to_sparse_vector(lexical_weights[0])
        return outputs

    raise ValueError("No supported vector output available from model")


def _run_chunk_search(search_terms: str, raw_limit: int):
    capabilities = cf.MODEL_CAPABILITIES
    query_vectors = encode_query(search_terms, capabilities)

    if "dense" in query_vectors and "sparse" in query_vectors:
        return client.query_points(
            collection_name=COLLECTION,
            prefetch=[
                Prefetch(
                    query=query_vectors["dense"],
                    using=DENSE_VECTOR_NAME,
                    limit=raw_limit,
                ),
                Prefetch(
                    query=query_vectors["sparse"],
                    using=SPARSE_VECTOR_NAME,
                    limit=raw_limit,
                ),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=raw_limit,
            with_payload=True,
        )

    if "dense" in query_vectors:
        return client.query_points(
            collection_name=COLLECTION,
            query=query_vectors["dense"],
            limit=raw_limit,
            with_payload=True,
        )

    if "sparse" in query_vectors:
        return client.query_points(
            collection_name=COLLECTION,
            query=query_vectors["sparse"],
            using=SPARSE_VECTOR_NAME,
            limit=raw_limit,
            with_payload=True,
        )

    raise ValueError("No query vectors generated")


def _group_best_chunk_per_document(points: list[Any]) -> list[dict[str, Any]]:
    grouped: Dict[str, dict[str, Any]] = {}

    for rank, point in enumerate(points):
        payload = point.payload or {}
        score = float(getattr(point, "score", 0.0) or 0.0)

        document_id = str(payload.get("document_id") or payload.get("filename") or f"doc_{rank}")
        filename = str(payload.get("filename") or document_id)
        chunk_index = int(payload.get("chunk_index", 0))
        chunk_count = int(payload.get("chunk_count", 1))
        chunk_text = str(payload.get("text", ""))

        current = grouped.get(document_id)
        if current is None or score > current["best_score"]:
            grouped[document_id] = {
                "document_id": document_id,
                "filename": filename,
                "best_score": score,
                "best_chunk_index": chunk_index,
                "best_chunk_count": chunk_count,
                "best_chunk_text": chunk_text,
            }

    return sorted(grouped.values(), key=lambda x: x["best_score"], reverse=True)


def _load_full_document(document_id: str) -> tuple[str, str]:
    all_points = []
    offset = None

    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION,
            scroll_filter=Filter(
                must=[
                    FieldCondition(
                        key="document_id",
                        match=MatchValue(value=document_id),
                    )
                ]
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

    ordered = sorted(
        all_points,
        key=lambda p: int((p.payload or {}).get("chunk_index", 0)),
    )

    if not ordered:
        return document_id, ""

    filename = str((ordered[0].payload or {}).get("filename", document_id))
    full_text = "\n".join(
        str((p.payload or {}).get("text", ""))
        for p in ordered
        if (p.payload or {}).get("text")
    ).strip()

    return filename, full_text


def retrieve_documents(
    search_terms: str,
    limit: int = 10,
    return_full_document: bool = True,
) -> List[Tuple[str, str, float]]:
    """
    Search over child chunks, group hits by document_id,
    keep the best child score for ranking,
    then return either the best chunk or the reconstructed full document.
    """
    if limit is None:
        limit = SEARCH_LIMIT

    try:
        raw_limit = max(limit * CANDIDATE_MULTIPLIER, MIN_CANDIDATES)
        results = _run_chunk_search(search_terms, raw_limit)
        points = getattr(results, "points", [])

        if not points:
            return []

        ranked_docs = _group_best_chunk_per_document(points)[:limit]

        final_results: List[Tuple[str, str, float]] = []

        for doc in ranked_docs:
            if return_full_document:
                filename, text = _load_full_document(doc["document_id"])
            else:
                filename = doc["filename"]
                text = doc["best_chunk_text"]

            final_results.append((filename, text, doc["best_score"]))

        return final_results

    except Exception as e:
        print(f"Error retrieving documents: {e}")
        return []


if __name__ == "__main__":
    query = input("Query: ").strip()
    results = retrieve_documents(query, limit=5, return_full_document=True)

    print("=" * 80)
    print(f"Results for: {query}")
    print("=" * 80)

    for i, (filename, text, score) in enumerate(results, start=1):
        preview = text[:800].replace("\n", " ")
        print(f"\n[{i}] {filename} | score={score:.4f}")
        print(preview)
        if len(text) > 800:
            print("...")
