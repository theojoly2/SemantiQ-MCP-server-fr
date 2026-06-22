from typing import List, Any, Tuple, Dict
from collections import defaultdict
import time
import multiprocessing
import os

# =====================================================================
# OPTIMISATION CPU (MAXIMUM POWER)
# =====================================================================
try:
    NUM_CORES = multiprocessing.cpu_count()
    # "Sweet spot" pour l'inférence par lots afin d'éviter le "Context Switch"
    OPTIMAL_THREADS = min(12, max(4, NUM_CORES // 2))
except NotImplementedError:
    OPTIMAL_THREADS = 4

os.environ["OMP_NUM_THREADS"] = str(OPTIMAL_THREADS)
os.environ["MKL_NUM_THREADS"] = str(OPTIMAL_THREADS)
os.environ["OPENBLAS_NUM_THREADS"] = str(OPTIMAL_THREADS)
os.environ["TOKENIZERS_PARALLELISM"] = "false"

try:
    import torch
    torch.set_num_threads(OPTIMAL_THREADS)
except ImportError:
    pass
# =====================================================================

from qdrant_client.models import (
    SparseVector,
    Filter,
    FieldCondition,
    MatchValue,
    MatchAny,
    PayloadSchemaType,
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

CANDIDATE_MULTIPLIER: int = int(
    getattr(cf, "config", {}).get("search", {}).get("candidate_multiplier", 8)
    if hasattr(cf, "config")
    else 8
)

MIN_CANDIDATES: int = int(
    getattr(cf, "config", {}).get("search", {}).get("min_candidates", 80)
    if hasattr(cf, "config")
    else 80
)

RERANK_POOL_SIZE: int = int(
    getattr(cf, "config", {}).get("search", {}).get("rerank_pool_size", 24)
    if hasattr(cf, "config")
    else 24
)

MAX_CHUNKS_PER_DOCUMENT: int = int(
    getattr(cf, "config", {}).get("search", {}).get("max_chunks_per_document", 3)
    if hasattr(cf, "config")
    else 3
)

AGGREGATION_TOP_K: int = int(
    getattr(cf, "config", {}).get("search", {}).get("aggregation_top_k", 3)
    if hasattr(cf, "config")
    else 3
)

AGGREGATION_MAX_WEIGHT: float = float(
    getattr(cf, "config", {}).get("search", {}).get("aggregation_max_weight", 0.9)
    if hasattr(cf, "config")
    else 0.9
)

AGGREGATION_MEAN_WEIGHT: float = float(
    getattr(cf, "config", {}).get("search", {}).get("aggregation_mean_weight", 0.1)
    if hasattr(cf, "config")
    else 0.1
)

HYBRID_DENSE_WEIGHT: float = float(
    getattr(cf, "config", {}).get("search", {}).get("hybrid_dense_weight", 0.7)
    if hasattr(cf, "config")
    else 0.7
)

_FILENAME_INDEX_CREATED = False


def detect_model_capabilities(model) -> dict[str, Any]:
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
            return_colbert_vecs=False
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


def _lexical_to_sparse_vector(lexical_weights: dict[Any, Any]):
    return SparseVector(
        indices=[int(k) for k in lexical_weights.keys()],
        values=[float(v) for v in lexical_weights.values()],
    )


def encode_query(query_text: str, capabilities: dict[str, Any]) -> dict[str, Any]:
    outputs: dict[str, Any] = {}
    if capabilities["has_dense"] and capabilities["has_sparse"]:
        result = model.encode(
            [query_text],
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False
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
        outputs["dense"] = first_dense.tolist() if hasattr(first_dense, "tolist") else list(first_dense)
        return outputs
    if capabilities["has_sparse"]:
        result = model.encode(
            [query_text],
            return_dense=False,
            return_sparse=True,
            return_colbert_vecs=False
            )
        lexical_weights = result.get("lexical_weights", [])
        if len(lexical_weights) > 0:
            outputs["sparse"] = _lexical_to_sparse_vector(lexical_weights[0])
        return outputs
    raise ValueError("No supported vector output available from model")


def _run_chunk_search(search_terms: str, raw_limit: int, tags: list = None):
    capabilities = cf.MODEL_CAPABILITIES
    query_vectors = encode_query(search_terms, capabilities)
    query_filter = None
    if tags:
        query_filter = Filter(must=[FieldCondition(key="tags", match=MatchAny(any=tags))])

    dense_res = None
    sparse_res = None
    if "dense" in query_vectors:
        dense_res = client.query_points(
            collection_name=COLLECTION, query=query_vectors["dense"], query_filter=query_filter,
            using=DENSE_VECTOR_NAME, limit=raw_limit, with_payload=True,
        )
    if "sparse" in query_vectors:
        sparse_res = client.query_points(
            collection_name=COLLECTION, query=query_vectors["sparse"], query_filter=query_filter,
            using=SPARSE_VECTOR_NAME, limit=raw_limit, with_payload=True,
        )

    if dense_res is None and sparse_res is None:
        raise ValueError("No query vectors generated")
    if sparse_res is None:
        return dense_res
    if dense_res is None:
        return sparse_res

    merged: dict[Any, dict[str, Any]] = {}
    for p in dense_res.points:
        merged[p.id] = {"point": p, "dense": float(p.score), "sparse": 0.0}
    for p in sparse_res.points:
        entry = merged.get(p.id)
        if entry is None:
            entry = {"point": p, "dense": 0.0, "sparse": 0.0}
            merged[p.id] = entry
        entry["sparse"] = float(p.score)

    def minmax(scores: dict[Any, float]) -> dict[Any, float]:
        if not scores:
            return {}
        vals = list(scores.values())
        vmin, vmax = min(vals), max(vals)
        if vmax <= vmin:
            return {k: 0.0 for k in scores.keys()}
        rng = vmax - vmin
        return {k: (v - vmin) / rng for k, v in scores.items()}

    dense_scores = {pid: v["dense"] for pid, v in merged.items()}
    sparse_scores = {pid: v["sparse"] for pid, v in merged.items()}
    dense_norm = minmax(dense_scores)
    sparse_norm = minmax(sparse_scores)
    alpha = HYBRID_DENSE_WEIGHT

    for pid, v in merged.items():
        d = dense_norm.get(pid, 0.0)
        s = sparse_norm.get(pid, 0.0)
        hybrid = alpha * d + (1.0 - alpha) * s
        v["hybrid"] = hybrid
        try:
            v["point"].score = hybrid
        except Exception:
            pass

    sorted_points = [
        merged[pid]["point"]
        for pid in sorted(merged.keys(), key=lambda pid: merged[pid]["hybrid"], reverse=True)
    ][:raw_limit]

    class QueryResult:
        def __init__(self, points):
            self.points = points

    return QueryResult(sorted_points)


def _limit_chunks_per_document(points: list[Any], max_chunks_per_document: int = MAX_CHUNKS_PER_DOCUMENT) -> list[Any]:
    counts: dict[str, int] = defaultdict(int)
    limited: list[Any] = []
    for point in points:
        payload = point.payload or {}
        document_id = str(payload.get("document_id") or payload.get("filename") or point.id)
        if counts[document_id] >= max_chunks_per_document:
            continue
        limited.append(point)
        counts[document_id] += 1
    return limited


def _compute_document_aggregated_score(scores: list[float], top_k: int = AGGREGATION_TOP_K, max_weight: float = AGGREGATION_MAX_WEIGHT, mean_weight: float = AGGREGATION_MEAN_WEIGHT) -> float:
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


def _group_best_chunk_per_document(points: list[Any]) -> list[dict[str, Any]]:
    grouped: Dict[str, dict[str, Any]] = {}
    for rank, point in enumerate(points):
        payload = point.payload or {}
        score = float(getattr(point, "score", 0.0) or 0.0)
        document_id = str(payload.get("document_id") or payload.get("filename") or f"doc_{rank}")
        filename = str(payload.get("filename") or document_id)
        chunk_index = int(payload.get("chunk_index", 0))
        chunk_text = str(payload.get("text", ""))

        current = grouped.get(document_id)
        if current is None:
            grouped[document_id] = {
                "document_id": document_id, "filename": filename, "scores": [score],
                "best_score": score, "best_chunk_index": chunk_index, "best_chunk_text": chunk_text,
            }
            continue
        current["scores"].append(score)
        if score > current["best_score"]:
            current["best_score"] = score
            current["best_chunk_index"] = chunk_index
            current["best_chunk_text"] = chunk_text

    aggregated_results: list[dict[str, Any]] = []
    for doc in grouped.values():
        doc = dict(doc)
        doc["aggregated_score"] = _compute_document_aggregated_score(
            scores=doc["scores"], top_k=AGGREGATION_TOP_K, max_weight=AGGREGATION_MAX_WEIGHT, mean_weight=AGGREGATION_MEAN_WEIGHT,
        )
        aggregated_results.append(doc)
    return sorted(aggregated_results, key=lambda x: (x["aggregated_score"], x["best_score"]), reverse=True)


def _resolve_chunk0_metadata_batch(document_ids: list[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not document_ids:
        return result
    try:
        t0 = time.time()
        print(f"[Qdrant] Fetching metadata for {len(document_ids)} documents...")
        points, _ = client.scroll(
            collection_name=COLLECTION,
            scroll_filter=Filter(
                must=[
                    FieldCondition(key="document_id", match=MatchAny(any=document_ids)),
                    FieldCondition(key="chunk_index", match=MatchValue(value=0)),
                ]
            ),
            limit=len(document_ids),
            with_payload=["doc_summary", "document_id"],
            with_vectors=False,
        )
        for p in points:
            payload = p.payload or {}
            doc_id = payload.get("document_id")
            if doc_id:
                result[doc_id] = {"id": p.id, "doc_summary": payload.get("doc_summary", "")}
        print(f"[Qdrant] Metadata fetch done in {time.time()-t0:.2f}s")
    except Exception as e:
        print(f"[!] Erreur lors de la récupération des métadonnées chunk 0 : {e}")
    return result


def retrieve_search_documents(
    search_terms: str,
    tags: list = None,
    limit: int = None,
) -> List[Tuple[str, str, str, Any, float, list]]:
    if limit is None:
        limit = RERANK_POOL_SIZE

    global _FILENAME_INDEX_CREATED
    if not _FILENAME_INDEX_CREATED:
        try:
            client.create_payload_index(
                collection_name=COLLECTION,
                field_name="filename",
                field_schema=PayloadSchemaType.TEXT
            )
            print("[Init] Text index on 'filename' ensured.")
        except Exception:
            pass
        finally:
            _FILENAME_INDEX_CREATED = True

    try:
        t_start = time.time()

        raw_limit = max(800, RERANK_POOL_SIZE * 15, limit * 20)

        print(f"\n[Search] Starting search for: '{search_terms}' (tags: {tags})")
        results = _run_chunk_search(search_terms, raw_limit, tags=tags)
        points = getattr(results, "points", [])

        if not points:
            print("[Search] Qdrant returned 0 points.")
            return []

        print(f"[Search] Qdrant returned {len(points)} raw chunks.")

        doc_tags_map = {}
        for p in points:
            payload = p.payload or {}
            doc_id = str(payload.get("document_id") or payload.get("filename") or p.id)
            if doc_id not in doc_tags_map:
                t = payload.get("tags", [])
                doc_tags_map[doc_id] = [t] if isinstance(t, str) else list(t)

        # On limite le nombre de chunks par document
        points = _limit_chunks_per_document(points, MAX_CHUNKS_PER_DOCUMENT)
        # On regroupe par document
        grouped_docs = _group_best_chunk_per_document(points)

        # =================================================================
        # RERANKING EN ENTONNOIR (CASCADE)
        # =================================================================

        # On prend EXACTEMENT "RERANK_POOL_SIZE" documents uniques
        candidate_docs = grouped_docs[:RERANK_POOL_SIZE]

        print(f"[Search] Extracted {len(grouped_docs)} UNIQUE documents from Qdrant.")
        print(f"[Search] Sending exactly Top {len(candidate_docs)} unique documents to Stage 1 Reranking.")

        doc_ids_needed = [doc["document_id"] for doc in candidate_docs]
        chunk0_metadata_map = _resolve_chunk0_metadata_batch(doc_ids_needed)

        for doc in candidate_docs:
            chunk0_info = chunk0_metadata_map.get(doc["document_id"], {})
            doc["chunk0_id"] = chunk0_info.get("id")
            doc["doc_summary"] = chunk0_info.get("doc_summary", "")

        # --- ÉTAPE 1 : RERANKING DES CHUNKS UNIQUEMENT ---
        chunk_pairs = []
        for doc in candidate_docs:
            filename_str = str(doc.get("filename", ""))
            doc_id = doc.get("document_id")
            doc_tags = doc_tags_map.get(doc_id, [])
            tags_str = ", ".join(doc_tags) if doc_tags else "Inconnue"

            chunk_text = str(doc.get("best_chunk_text", ""))[:1024]
            enriched_chunk = f"Source : {tags_str}\nFichier : {filename_str}\nContenu : {chunk_text}"

            chunk_pairs.append([search_terms, enriched_chunk])

        if chunk_pairs:
            print(f"[Rerank] Stage 1: Scoring {len(chunk_pairs)} Chunks...")
            t0 = time.time()
            chunk_scores = []
            batch_size = 24
            for i in range(0, len(chunk_pairs), batch_size):
                batch = chunk_pairs[i:i + batch_size]
                batch_scores = reranker.compute_score(batch, normalize=True)

                if not isinstance(batch_scores, (list, tuple)):
                    batch_scores = batch_scores.tolist() if hasattr(batch_scores, "tolist") else [batch_scores]
                chunk_scores.extend(batch_scores)

            for i, doc in enumerate(candidate_docs):
                doc["rerank_score"] = float(chunk_scores[i]) if i < len(chunk_scores) else 0.0

            # Tri des documents par score du chunk
            candidate_docs.sort(key=lambda x: x["rerank_score"], reverse=True)
            print(f"[Rerank] Stage 1 done in {time.time()-t0:.2f}s")

        # --- ÉTAPE 2 : RERANKING HYBRIDE AVEC LES RÉSUMÉS SUR LES 30 FINALISTES UNIQUES ---
        top_n = min(30, len(candidate_docs))
        finalists = candidate_docs[:top_n]

        summary_pairs = []
        for doc in finalists:
            filename_str = str(doc.get("filename", ""))
            doc_id = doc.get("document_id")
            doc_tags = doc_tags_map.get(doc_id, [])
            tags_str = ", ".join(doc_tags) if doc_tags else "Inconnue"

            summary = str(doc.get("doc_summary", "")).strip()
            if not summary:
                summary = str(doc.get("best_chunk_text", ""))

            enriched_summary = f"Source : {tags_str}\nFichier : {filename_str}\nRésumé : {summary[:1024]}"
            summary_pairs.append([search_terms, enriched_summary])

        if summary_pairs:
            print(f"[Rerank] Stage 2: Scoring {len(summary_pairs)} Summaries for Top {top_n} uniques...")
            t0 = time.time()
            summary_scores = []

            for i in range(0, len(summary_pairs), batch_size):
                batch = summary_pairs[i:i + batch_size]
                batch_scores = reranker.compute_score(batch, normalize=True)

                if not isinstance(batch_scores, (list, tuple)):
                    batch_scores = batch_scores.tolist() if hasattr(batch_scores, "tolist") else [batch_scores]
                summary_scores.extend(batch_scores)

            # Mélange des notes : 50% Chunk (déjà calculé) / 50% Résumé
            for i, doc in enumerate(finalists):
                if i < len(summary_scores):
                    score_chunk = doc["rerank_score"]
                    score_summary = float(summary_scores[i])
                    doc["rerank_score"] = (0.5 * score_chunk) + (0.5 * score_summary)

            # Tri définitif
            finalists.sort(key=lambda x: x["rerank_score"], reverse=True)
            print(f"[Rerank] Stage 2 done in {time.time()-t0:.2f}s")

            candidate_docs = finalists

        # 4. Découpage final pour l'UI
        final_docs = candidate_docs[:limit]

        final_results: List[Tuple[str, str, str, Any, float, list]] = []
        for doc in final_docs:
            final_results.append((
                doc["filename"], doc.get("best_chunk_text", ""),
                doc.get("doc_summary", "") or "Aucun résumé",
                doc.get("chunk0_id"), float(doc.get("rerank_score", 0.0)), doc_tags_map.get(doc["document_id"], [])
            ))

        print(f"[Search] Process entirely completed in {time.time()-t_start:.2f}s")
        return final_results

    except Exception as e:
        import traceback
        print(f"\n[!!! ERROR !!!] Crash in retrieve_search_documents: {e}")
        traceback.print_exc()
        return []


if __name__ == "__main__":
    query = input("Query: ").strip()
    results = retrieve_search_documents(query, limit=8)

    print("=" * 80)
    print(f"Results for: {query}")
    print("=" * 80)

    for i, (filename, best_chunk_text, doc_summary, chunk0_id, score, tags) in enumerate(results, start=1):
        print(f"\n[{i}] {filename} | chunk_0_id={chunk0_id} | rerank_score={score:.4f} | tags={tags}")
        print(f"SUMMARY: {doc_summary}")
