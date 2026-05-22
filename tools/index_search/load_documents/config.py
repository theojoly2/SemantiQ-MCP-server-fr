from os import getenv
from typing import Any

from pathlib import Path
from yaml import safe_load
from qdrant_client import QdrantClient
from dotenv import load_dotenv

from typing import List, Tuple, Union
import torch
from transformers import AutoTokenizer, AutoModelForSequenceClassification, AutoModel

load_dotenv()

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

with CONFIG_PATH.open("r", encoding="utf-8") as f:
    config = safe_load(f)


class HFTransformerReranker:
    """
    Reranker basé sur Hugging Face Transformers.

    Supporte :
    - les modèles de type BAAI/bge-reranker-* via AutoModelForSequenceClassification,
    - le modèle jinaai/jina-reranker-v3 via AutoModel(trust_remote_code=True)
      et sa méthode listwise `model.rerank(query, documents, ...)`.
    """

    def __init__(self, model_name: str, use_fp16: bool = True, device: str = None):
        self.model_name = model_name

        # Détection simple du mode Jina v3 listwise
        self.is_jina_listwise = "jina-reranker-v3" in model_name

        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device

        if self.is_jina_listwise:
            # Jina v3 : modèle avec code distant et méthode .rerank(query, documents, ...)
            self.tokenizer = None
            self.model = AutoModel.from_pretrained(
                model_name,
                trust_remote_code=True,
                torch_dtype="auto",
            )
        else:
            # BGE & autres rerankers "classiques" (sequence classification)
            self.tokenizer = AutoTokenizer.from_pretrained(model_name)
            self.model = AutoModelForSequenceClassification.from_pretrained(model_name)

        self.model.eval()
        self.model.to(self.device)

        self.use_fp16 = use_fp16 and (self.device == "cuda")
        if self.use_fp16:
            self.model = self.model.to(dtype=torch.float16)

    @torch.inference_mode()
    def compute_score(
        self,
        sentence_pairs: Union[Tuple[str, str], List[Tuple[str, str]]],
        normalize: bool = False,
        max_length: int = 512,  # garde le param pour BGE, mais PAS utilisé pour Jina v3
    ):
        # Autoriser un seul couple ou une liste de couples
        if isinstance(sentence_pairs, tuple):
            pairs = [sentence_pairs]
        else:
            pairs = list(sentence_pairs)

        # ------- Mode Jina v3 : listwise reranking via model.rerank(query, documents) -------
        if self.is_jina_listwise:
            # On suppose le cas classique "une seule requête, plusieurs documents"
            queries = [q for q, _ in pairs]
            docs = [d for _, d in pairs]

            unique_queries = list(dict.fromkeys(queries))
            if len(unique_queries) != 1:
                raise ValueError(
                    "HFTransformerReranker (jina-reranker-v3) attend une seule query "
                    "pour plusieurs documents. Toutes les paires doivent partager la même query."
                )
            query = unique_queries[0]

            # Signature officielle v3 : rerank(query, documents, top_n=None, return_embeddings=False)
            # → pas de max_length / max_doc_length ici. [web:296]
            result = self.model.rerank(
                query=query,
                documents=docs,
                top_n=None,             # on garde tout
                return_embeddings=False,
            )

            # On reconstruit une liste de scores alignée sur l’ordre des docs
            scores = [0.0] * len(docs)
            for item in result:
                idx = item.get("index")
                score = item.get("relevance_score", item.get("score", 0.0))
                if idx is not None and 0 <= idx < len(docs):
                    scores[idx] = float(score)

            if isinstance(sentence_pairs, tuple):
                return scores[0]
            return scores

        # ------- Mode BGE / cross-encoder classique : pairwise (query, doc) → logit -------
        queries = [q for q, _ in pairs]
        docs = [d for _, d in pairs]

        inputs = self.tokenizer(
            queries,
            docs,
            padding=True,
            truncation=True,
            max_length=max_length,
            return_tensors="pt",
        ).to(self.device)

        logits = self.model(**inputs).logits.view(-1)

        scores = logits.float()
        if normalize:
            scores = torch.sigmoid(scores)

        scores = scores.cpu().tolist()
        if isinstance(sentence_pairs, tuple):
            return scores[0]
        return scores


def _load_qdrant_client() -> QdrantClient:
    qdrant_cfg = config["qdrant"]

    api_key_env_name = qdrant_cfg.get("api_key")
    api_key_value = getenv(api_key_env_name) if api_key_env_name else None

    return QdrantClient(
        host=qdrant_cfg["host"],
        port=qdrant_cfg["port"],
        timeout=qdrant_cfg.get("timeout", 120),
        api_key=api_key_value,
        https=qdrant_cfg.get("https", False),
        check_compatibility=qdrant_cfg.get("check_compatibility", False),
    )


def _load_embedding_model():
    model_embedding = config["model"]["embedding"]

    if "bge-m3" in model_embedding.lower():
        from FlagEmbedding import BGEM3FlagModel

        model = BGEM3FlagModel(
            model_embedding,
            use_fp16=True,
        )
        print(f"✓ Loaded hybrid embedding model: {model_embedding}")
        return model

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_embedding)
    print(f"✓ Loaded dense embedding model: {model_embedding}")
    return model


def _load_reranker_model():
    model_reranker = config["model"]["reranker"]

    reranker = HFTransformerReranker(
        model_name=model_reranker,
        use_fp16=True,
    )
    print(f"✓ Loaded reranker model (HF): {model_reranker}")
    return reranker


def _detect_model_capabilities(model) -> dict[str, Any]:
    """
    Detect whether model supports dense, sparse, or both.
    Runs once at startup.
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
        raise ValueError(
            f"Could not detect model capabilities for configured embedding model: {e}"
        ) from e


client = _load_qdrant_client()

EMBEDDING_MODEL_NAME = config["model"]["embedding"]
RERANKER_MODEL_NAME = config["model"]["reranker"]

EMBEDDING_MODEL = _load_embedding_model()
RERANKER_MODEL = _load_reranker_model()

MODEL_CAPABILITIES = _detect_model_capabilities(EMBEDDING_MODEL)

# Backward compatibility with existing code
model = EMBEDDING_MODEL
reranker = RERANKER_MODEL

# Qdrant collection name used for indexing and retrieval.
COLLECTION = config["collection"]["name"]

# Number of chunks uploaded per indexing batch.
BATCH_SIZE = int(config["indexing"].get("batch_size", 5))
BATCHSIZE = BATCH_SIZE

# Root directory containing source documents to index.
DOCUMENTS_PATH = config["indexing"].get("documents_path", "documents")

# Maximum number of final search results returned.
SEARCH_LIMIT = int(config.get("search", {}).get("limit", 3))

# Oversampling factor used to fetch more raw candidates before final selection.
CANDIDATE_MULTIPLIER = int(config.get("search", {}).get("candidate_multiplier", 10))

# Minimum number of raw candidates to inspect before post-processing.
MIN_CANDIDATES = int(config.get("search", {}).get("min_candidates", 30))

# Maximum number of chunks scanned for a single document during contextual reconstruction.
SCROLL_LIMIT_PER_DOC = int(config.get("search", {}).get("scroll_limit_per_doc", 10000))

# Number of neighboring chunks fetched on each side of a relevant chunk.
WINDOW_RADIUS = int(config.get("search", {}).get("window_radius", 4))

# If a document has at most this many chunks,
# the full document can be reconstructed instead of a local window.
FULL_DOCUMENT_CHUNK_THRESHOLD = int(
    config.get("search", {}).get("full_document_chunk_threshold", 12)
)

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"

print(
    "✓ Embedding model capabilities:",
    {
        "has_dense": MODEL_CAPABILITIES["has_dense"],
        "has_sparse": MODEL_CAPABILITIES["has_sparse"],
        "dense_dim": MODEL_CAPABILITIES["dense_dim"],
    },
)
print(f"✓ Reranker ready: {RERANKER_MODEL_NAME}")
