from dotenv import load_dotenv
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.tools import Tool
from fastmcp.server.event_store import EventStore
import tools

project_dir = Path(__file__).resolve().parent
env_path = project_dir / ".env"

load_dotenv(dotenv_path=env_path)

# CHANGE FOR DEPLOYMENT/DEVELOPMENT!
mcp = FastMCP(
    name="...",
)

mcp.add_tool(
    Tool.from_function(
        tools.retrieve_search_documents,
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
