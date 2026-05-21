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
reranker = cf.reranker
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

AGGREGATION_TOP_K: int = int(
    getattr(cf, "config", {}).get("search", {}).get("aggregation_top_k", 3)
    if hasattr(cf, "config")
    else 3
)

AGGREGATION_MAX_WEIGHT: float = float(
    getattr(cf, "config", {}).get("search", {}).get("aggregation_max_weight", 0.8)
    if hasattr(cf, "config")
    else 0.8
)

AGGREGATION_MEAN_WEIGHT: float = float(
    getattr(cf, "config", {}).get("search", {}).get("aggregation_mean_weight", 0.2)
    if hasattr(cf, "config")
    else 0.2
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
    """
    Recherche hybride dense + sparse avec fusion pondérée:
    score_hybrid = 0.7 * dense_norm + 0.3 * sparse_norm
    """
    capabilities = cf.MODEL_CAPABILITIES
    query_vectors = encode_query(search_terms, capabilities)

    dense_res = None
    sparse_res = None

    # 1) Requête dense-only si dispo
    if "dense" in query_vectors:
        dense_res = client.query_points(
            collection_name=COLLECTION,
            query=query_vectors["dense"],
            using=DENSE_VECTOR_NAME,  # "dense"
            limit=raw_limit,
            with_payload=True,
        )

    # 2) Requête sparse-only si dispo
    if "sparse" in query_vectors:
        sparse_res = client.query_points(
            collection_name=COLLECTION,
            query=query_vectors["sparse"],
            using=SPARSE_VECTOR_NAME,  # "sparse"
            limit=raw_limit,
            with_payload=True,
        )

    # Si aucune des deux modalités n'est dispo
    if dense_res is None and sparse_res is None:
        raise ValueError("No query vectors generated")

    # Si une seule modalité est dispo, on renvoie directement son résultat
    if sparse_res is None:
        return dense_res
    if dense_res is None:
        return sparse_res

    # 3) Fusion dense + sparse avec pondération 0.7 / 0.3

    # Regrouper par id de point
    merged: dict[Any, dict[str, Any]] = {}

    for p in dense_res.points:
        merged[p.id] = {
            "point": p,
            "dense": float(p.score),
            "sparse": 0.0,
        }

    for p in sparse_res.points:
        entry = merged.get(p.id)
        if entry is None:
            entry = {
                "point": p,
                "dense": 0.0,
                "sparse": 0.0,
            }
            merged[p.id] = entry
        entry["sparse"] = float(p.score)

    # Min-max normalisation pour chaque modalité
    def minmax(scores: dict[Any, float]) -> dict[Any, float]:
        if not scores:
            return {}
        vals = list(scores.values())
        vmin, vmax = min(vals), max(vals)
        if vmax <= vmin:
            # tous égaux => on met tout à 0
            return {k: 0.0 for k in scores.keys()}
        rng = vmax - vmin
        return {k: (v - vmin) / rng for k, v in scores.items()}

    dense_scores = {pid: v["dense"] for pid, v in merged.items()}
    sparse_scores = {pid: v["sparse"] for pid, v in merged.items()}

    dense_norm = minmax(dense_scores)
    sparse_norm = minmax(sparse_scores)

    alpha = 0.7  # poids du dense
    for pid, v in merged.items():
        d = dense_norm.get(pid, 0.0)
        s = sparse_norm.get(pid, 0.0)
        v["hybrid"] = alpha * d + (1.0 - alpha) * s

    # 4) Trier selon le score hybride et tronquer à raw_limit
    sorted_points = [
        merged[pid]["point"]
        for pid in sorted(
            merged.keys(),
            key=lambda pid: merged[pid]["hybrid"],
            reverse=True,
        )
    ][:raw_limit]

    # 5) Retourner un objet compatible avec l'usage existant:
    #     results = _run_chunk_search(...)
    #     points = getattr(results, "points", [])
    class QueryResult:
        def __init__(self, points):
            self.points = points

    return QueryResult(sorted_points)


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
        if current is None:
            grouped[document_id] = {
                "document_id": document_id,
                "filename": filename,
                "scores": [score],
                "best_score": score,
                "best_chunk_index": chunk_index,
                "best_chunk_count": chunk_count,
                "best_chunk_text": chunk_text,
            }
            continue

        current["scores"].append(score)

        if score > current["best_score"]:
            current["best_score"] = score
            current["best_chunk_index"] = chunk_index
            current["best_chunk_count"] = chunk_count
            current["best_chunk_text"] = chunk_text

    aggregated_results: list[dict[str, Any]] = []

    for doc in grouped.values():
        doc = dict(doc)
        doc["aggregated_score"] = _compute_document_aggregated_score(
            scores=doc["scores"],
            top_k=AGGREGATION_TOP_K,
            max_weight=AGGREGATION_MAX_WEIGHT,
            mean_weight=AGGREGATION_MEAN_WEIGHT,
        )
        aggregated_results.append(doc)

    return sorted(
        aggregated_results,
        key=lambda x: (x["aggregated_score"], x["best_score"]),
        reverse=True,
    )


def _load_document_chunks(document_id: str) -> list[Any]:
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

    return sorted(
        all_points,
        key=lambda p: int((p.payload or {}).get("chunk_index", 0)),
    )


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


def _load_full_document(document_id: str) -> tuple[str, str]:
    ordered = _load_document_chunks(document_id)

    if not ordered:
        return document_id, ""

    filename = str((ordered[0].payload or {}).get("filename", document_id))
    full_text = "\n".join(
        str((p.payload or {}).get("text", ""))
        for p in ordered
        if (p.payload or {}).get("text")
    ).strip()

    return filename, full_text


def _load_document_window(
    document_id: str,
    best_chunk_index: int,
    window_radius: int = WINDOW_RADIUS,
) -> tuple[str, str]:
    ordered = _load_document_chunks(document_id)

    if not ordered:
        return document_id, ""

    filename = str((ordered[0].payload or {}).get("filename", document_id))
    total_chunks = len(ordered)

    start_idx = max(0, best_chunk_index - window_radius)
    end_idx = min(total_chunks - 1, best_chunk_index + window_radius)

    selected = [
        p for p in ordered
        if start_idx <= int((p.payload or {}).get("chunk_index", 0)) <= end_idx
    ]

    partial_text = "\n".join(
        str((p.payload or {}).get("text", ""))
        for p in selected
        if (p.payload or {}).get("text")
    ).strip()

    header = _build_partial_header(
        filename=filename,
        best_chunk_index=best_chunk_index,
        start_idx=start_idx,
        end_idx=end_idx,
        total_chunks=total_chunks,
    )

    return filename, header + partial_text


def _load_best_view_for_document(
    document_id: str,
    best_chunk_index: int,
    return_full_document: bool = True,
) -> tuple[str, str]:
    ordered = _load_document_chunks(document_id)

    if not ordered:
        return document_id, ""

    filename = str((ordered[0].payload or {}).get("filename", document_id))
    total_chunks = len(ordered)

    if not return_full_document:
        for p in ordered:
            payload = p.payload or {}
            if int(payload.get("chunk_index", 0)) == best_chunk_index:
                return filename, str(payload.get("text", ""))
        return filename, ""

    if total_chunks <= FULL_DOCUMENT_CHUNK_THRESHOLD:
        full_text = "\n".join(
            str((p.payload or {}).get("text", ""))
            for p in ordered
            if (p.payload or {}).get("text")
        ).strip()
        return filename, full_text

    start_idx = max(0, best_chunk_index - WINDOW_RADIUS)
    end_idx = min(total_chunks - 1, best_chunk_index + WINDOW_RADIUS)

    selected = [
        p for p in ordered
        if start_idx <= int((p.payload or {}).get("chunk_index", 0)) <= end_idx
    ]

    partial_text = "\n".join(
        str((p.payload or {}).get("text", ""))
        for p in selected
        if (p.payload or {}).get("text")
    ).strip()

    header = _build_partial_header(
        filename=filename,
        best_chunk_index=best_chunk_index,
        start_idx=start_idx,
        end_idx=end_idx,
        total_chunks=total_chunks,
    )

    return filename, header + partial_text


def _rerank_documents(
    query: str,
    docs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not docs:
        return []

    pairs = [
        [query, doc.get("rerank_text", "")]
        for doc in docs
    ]

    scores = reranker.compute_score(pairs)

    if not isinstance(scores, list):
        scores = [scores]

    reranked = []
    for doc, rerank_score in zip(docs, scores):
        item = dict(doc)
        item["rerank_score"] = float(rerank_score)
        reranked.append(item)

    reranked.sort(
        key=lambda x: (x["rerank_score"], x["best_score"]),
        reverse=True,
    )
    return reranked


def _compute_document_aggregated_score(
    scores: list[float],
    top_k: int = AGGREGATION_TOP_K,
    max_weight: float = AGGREGATION_MAX_WEIGHT,
    mean_weight: float = AGGREGATION_MEAN_WEIGHT,
) -> float:
    if not scores:
        return 0.0

    sorted_scores = sorted(scores, reverse=True)
    best_score = sorted_scores[0]

    top_scores = sorted_scores[:max(1, top_k)]
    mean_top_score = sum(top_scores) / len(top_scores)

    weight_sum = max_weight + mean_weight
    if weight_sum <= 0:
        return best_score

    max_weight = max_weight / weight_sum
    mean_weight = mean_weight / weight_sum

    return (max_weight * best_score) + (mean_weight * mean_top_score)


def retrieve_documents(
    search_terms: str,
    limit: int = 10,
    return_full_document: bool = True,
) -> List[Tuple[str, str, float]]:
    """
    Search over child chunks, group hits by document_id,
    retrieve candidate document views,
    rerank them with a cross-encoder,
    then return the best reranked documents.
    """
    if limit is None:
        limit = SEARCH_LIMIT

    try:
        raw_limit = max(limit * CANDIDATE_MULTIPLIER, MIN_CANDIDATES)
        results = _run_chunk_search(search_terms, raw_limit)
        points = getattr(results, "points", [])

        if not points:
            return []

        grouped_docs = _group_best_chunk_per_document(points)

        candidate_docs: list[dict[str, Any]] = []
        for doc in grouped_docs:
            filename, text = _load_best_view_for_document(
                document_id=doc["document_id"],
                best_chunk_index=doc["best_chunk_index"],
                return_full_document=return_full_document,
            )

            candidate_docs.append({
                **doc,
                "filename": filename,
                "text": text,
                "rerank_text": text,
            })

        reranked_docs = _rerank_documents(search_terms, candidate_docs)[:limit]

        final_results: List[Tuple[str, str, float]] = []
        for doc in reranked_docs:
            final_results.append(
                (doc["filename"], doc["text"], doc["rerank_score"])
            )

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
