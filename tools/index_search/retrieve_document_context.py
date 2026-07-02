from typing import List, Any, Tuple
from collections import defaultdict
from functools import lru_cache

from qdrant_client.models import (
    SparseVector,
    Filter,
    FieldCondition,
    MatchValue,
    MatchAny,
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
HYBRID_DENSE_WEIGHT: float = float(getattr(cf, "config", {}).get("search", {}).get("hybrid_dense_weight", 0.7) if hasattr(cf, "config") else 0.7)


def _lexical_to_sparse_vector(lexical_weights: dict[Any, Any]):
    return SparseVector(
        indices=[int(k) for k in lexical_weights.keys()],
        values=[float(v) for v in lexical_weights.values()],
    )


@lru_cache(maxsize=512)
def _cached_encode(query_text: str) -> dict[str, Any]:
    """Encode la question et met en cache pour éviter de recalculer les mêmes requêtes"""
    capabilities = cf.MODEL_CAPABILITIES
    outputs: dict[str, Any] = {}

    if capabilities["has_dense"] and capabilities["has_sparse"]:
        result = model.encode([query_text], return_dense=True, return_sparse=True, return_colbert_vecs=False)
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
        result = model.encode([query_text], return_dense=False, return_sparse=True, return_colbert_vecs=False)
        lexical_weights = result.get("lexical_weights", [])
        if len(lexical_weights) > 0:
            outputs["sparse"] = _lexical_to_sparse_vector(lexical_weights[0])
        return outputs

    raise ValueError("No supported vector output available from model")


def retrieve_document_context(document_id: str, query: str, top_k: int = 3, window_size: int = 3) -> str:
    """
    1. Fait une recherche vectorielle restreinte à un document_id.
    2. Récupère les `top_k` meilleurs chunks.
    3. Étend la sélection à +/- `window_size` chunks autour des meilleurs.
    4. Fusionne les intervalles qui se chevauchent.
    5. Reconstruit le texte final proprement.
    """

    # 1. Vectorisation de la requête (historique + question)
    query_vectors = _cached_encode(query)

    # Filtre strict : on ne cherche QUE dans ce document
    doc_filter = Filter(must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))])

    # 2. Recherche Qdrant (Top K) - On limite à 10 pour le score hybride, on gardera le Top 3 ensuite
    dense_res, sparse_res = None, None
    if "dense" in query_vectors:
        dense_res = cf.client.query_points(
            collection_name=cf.COLLECTION, query=query_vectors["dense"], query_filter=doc_filter,
            using=DENSE_VECTOR_NAME, limit=10, with_payload=["chunk_index"]
        )
    if "sparse" in query_vectors:
        sparse_res = cf.client.query_points(
            collection_name=cf.COLLECTION, query=query_vectors["sparse"], query_filter=doc_filter,
            using=SPARSE_VECTOR_NAME, limit=10, with_payload=["chunk_index"]
        )

    # Fusion hybride très simplifiée (comme dans ton code)
    merged = {}
    if dense_res:
        for p in dense_res.points:
            merged[p.id] = {"point": p, "dense": p.score, "sparse": 0.0}
    if sparse_res:
        for p in sparse_res.points:
            if p.id not in merged:
                merged[p.id] = {"point": p, "dense": 0.0, "sparse": 0.0}
            merged[p.id]["sparse"] = p.score

    # Si rien n'est trouvé
    if not merged:
        return "Aucune information pertinente trouvée dans ce document pour répondre à la question."

    # Normalisation min-max simple pour l'hybride
    dense_vals = [v["dense"] for v in merged.values()]
    sparse_vals = [v["sparse"] for v in merged.values()]

    d_min, d_max = (min(dense_vals), max(dense_vals)) if dense_vals else (0, 0)
    s_min, s_max = (min(sparse_vals), max(sparse_vals)) if sparse_vals else (0, 0)

    for pid, v in merged.items():
        d_norm = (v["dense"] - d_min) / (d_max - d_min) if d_max > d_min else 0
        s_norm = (v["sparse"] - s_min) / (s_max - s_min) if s_max > s_min else 0
        v["hybrid"] = (HYBRID_DENSE_WEIGHT * d_norm) + ((1.0 - HYBRID_DENSE_WEIGHT) * s_norm)

    # Récupérer les indices des Top 3 meilleurs chunks
    sorted_points = sorted(merged.values(), key=lambda x: x["hybrid"], reverse=True)[:top_k]
    best_indices = [int(p["point"].payload.get("chunk_index", 0)) for p in sorted_points]

    # 3. Calculer les intervalles avec fenêtre de contexte (+/- 3)
    intervals = []
    for idx in best_indices:
        intervals.append((max(0, idx - window_size), idx + window_size))

    # 4. Fusionner les intervalles qui se chevauchent (ou qui sont adjacents)
    intervals.sort(key=lambda x: x[0])
    merged_intervals = []

    for current in intervals:
        if not merged_intervals:
            merged_intervals.append(current)
        else:
            last = merged_intervals[-1]
            # Si le début de l'actuel chevauche ou touche la fin du précédent (+1 pour recoller les morceaux)
            if current[0] <= last[1] + 1:
                # On met à jour la fin du dernier intervalle
                merged_intervals[-1] = (last[0], max(last[1], current[1]))
            else:
                merged_intervals.append(current)

    # Extraire la liste exacte de tous les indices dont on a besoin
    needed_indices = []
    for start, end in merged_intervals:
        needed_indices.extend(list(range(start, end + 1)))

    # 5. Récupérer le texte de tous ces chunks en une seule requête Qdrant
    points_context, _ = cf.client.scroll(
        collection_name=cf.COLLECTION,
        scroll_filter=Filter(
            must=[
                FieldCondition(key="document_id", match=MatchValue(value=document_id)),
                FieldCondition(key="chunk_index", match=MatchAny(any=needed_indices)),
            ]
        ),
        limit=len(needed_indices), # On s'assure de tout récupérer
        with_payload=["chunk_index", "text"],
        with_vectors=False,
    )

    # Trier par ordre d'apparition dans le document
    points_context.sort(key=lambda p: int(p.payload.get("chunk_index", 0)))

    # 6. Reconstruire le texte complet
    final_text_parts = []
    last_idx = None

    for p in points_context:
        idx = int(p.payload.get("chunk_index", 0))
        text = str(p.payload.get("text", "")).strip()

        if last_idx is not None:
            # S'il y a un trou entre le chunk précédent et celui-ci (ex: chunk 5 puis chunk 15)
            if idx > last_idx + 1:
                final_text_parts.append("\n\n[...]\n\n")
            else:
                final_text_parts.append("\n")

        final_text_parts.append(text)
        last_idx = idx

    return "".join(final_text_parts)
