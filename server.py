import os

from dotenv import load_dotenv
from pathlib import Path
import asyncio
from concurrent.futures import ThreadPoolExecutor
import functools

from fastmcp import FastMCP
from fastmcp.tools import Tool
from fastmcp.server.event_store import EventStore
import tools

# Désactive le parallélisme interne du Tokenizer HuggingFace pour éviter les deadlocks
os.environ["TOKENIZERS_PARALLELISM"] = "false"

project_dir = Path(__file__).resolve().parent
env_path = project_dir / ".env"
load_dotenv(dotenv_path=env_path)

# ====================================================================
# 1. CRÉATION DU POOL DE THREADS (LE "SWEET SPOT" FINAL)
# max_workers=2 : On autorise 2 recherches SIMULTANÉES.
# Sachant que PyTorch est limité à 12 threads par le script de recherche,
# cela fera 24 cœurs actifs sur vos 30. C'est l'équilibre parfait entre
# un temps d'attente nul pour les utilisateurs et un processeur qui respire !
# ====================================================================
ai_thread_pool = ThreadPoolExecutor(max_workers=2)

mcp = FastMCP(
    name="ServeurSemantique",
)


# ====================================================================
# 2. CRÉATION DE LA FONCTION WRAPPER ASYNCHRONE
# ====================================================================
async def async_retrieve_search_documents(search_terms: str, limit: int = 20, tags: list = None) -> dict:
    loop = asyncio.get_running_loop()

    func = functools.partial(
        tools.retrieve_search_documents,
        search_terms=search_terms,
        tags=tags,
        limit=limit
    )

    # Exécution dans le thread pool
    results = await loop.run_in_executor(ai_thread_pool, func)

    # Formatage JSON sécurisé
    return {"result": results}


# --- ENREGISTREMENT DES OUTILS ---
mcp.add_tool(
    Tool.from_function(
        async_retrieve_search_documents,
        name="retrieve_search_documents",
    )
)

mcp.add_tool(
    Tool.from_function(
        tools.get_available_tags,
        name="get_available_tags",
    )
)

mcp.add_tool(
    Tool.from_function(
        tools.get_document_file,
        name="get_document_file",
    )
)

event_store = EventStore()
app = mcp.http_app(
    event_store=event_store,
    retry_interval=2000,
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
