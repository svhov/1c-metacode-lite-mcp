"""1C Metacode MCP Server — lightweight edition.

Entry point: loads data into Memgraph, then starts FastMCP server with SSE transport.
"""

import logging
import sys
import threading

from fastmcp import FastMCP

from app.config import PROJECT_NAME, MCP_PORT, ENABLE_EMBEDDING
from app.db.loader import load_all
from app.db.connection import close as close_db
from app.tools.search_metadata import handle_search_metadata
from app.tools.search_code import handle_search_code
from app.tools.search_description import handle_search_description
from app.tools.search_embedding import handle_search_embedding

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

mcp = FastMCP(f"1c-metacode-{PROJECT_NAME}")


# ── Tool 1: search_metadata (structural graph queries + code search) ──

SEARCH_METADATA_DESC = """Search 1C metadata graph and BSL code. Send JSON with "op" field.

Structural operations (14):
- browse: list categories, objects. Params: category, name
- object_structure: full object overview. Params: name
- get_children: attributes, resources, dimensions, tabular parts, enum values, commands, layouts. Params: name, child_type (Attribute|Resource|Dimension|Characteristic|TabularPart|EnumValue|Command|Layout), tabular
- find_by_child: find objects by tabular part or attribute name. Params: tabular, attribute
- get_form: forms, controls, events, bindings. Params: name, form, detail (controls|events|attributes|commands|bindings|all), limit
- get_routines: find routines. Params: name, module, signature, export, unused
- get_routine_body: get routine source code. Params: name or id, owner
- get_call_graph: callers, callees, call tree. Params: name or id, direction (callers|callees|tree), from/to (cross-owner)
- get_predefined: predefined items. Params: name, item, is_folder
- get_access: role access rights. Params: role, target
- get_references: USED_IN and DO_MOVEMENTS_IN edges. Params: name, direction (incoming|outgoing|movements)
- get_subscriptions: event subscriptions. Params: name
- get_http_service: HTTP services, URL templates, methods. Params: name, template
- resolve: resolve by qualified_name, GUID, prefix. Params: qn, guid, prefix

Code search operations:
- find_routines_by_description: {"op": "find_routines_by_description", "text": "HTTP", "export": true}

Examples:
{"op": "browse", "name": "Контрагент"}
{"op": "get_children", "name": "РеализацияТоваров", "child_type": "Attribute"}
{"op": "get_routines", "name": "ОтправитьЗапрос"}
{"op": "get_references", "name": "Контрагенты", "direction": "incoming"}
{"op": "find_routines_by_description", "text": "штрихкод"}
"""


@mcp.tool(description=SEARCH_METADATA_DESC)
def search_metadata(query: str) -> str:
    """Search 1C metadata graph and BSL code by template operations."""
    import json
    try:
        req = json.loads(query)
    except json.JSONDecodeError:
        return handle_search_metadata(query)
    op = req.get("op", "")
    # Route code-search ops to search_code handler
    if op in ("find_routines_by_description", "get_routine_body"):
        return handle_search_code(query)
    return handle_search_metadata(query)


# ── Tool 2: search_by_embedding (semantic search) ──

SEARCH_EMBEDDING_DESC = """Semantic search — find routines and metadata objects by meaning (not exact name).
Use when you don't know the exact 1C object/procedure name but can describe what it does.
Also searches by Synonym, Comment, and name (fulltext fallback).

Operations:
- search_routines: {"op": "search_routines", "text": "работа со штрихкодами и кодированием"}
- search_objects: {"op": "search_objects", "text": "справочник видов операций с документами"}
- search_all: {"op": "search_all", "text": "печать наклейки со штрихкодом"}
- search_metadata_by_description: {"op": "search_metadata_by_description", "text": "документы предприятия"}

Tip: describe WHAT the object does, not its technical name.
Good: "справочник видов операций с документами"
Bad: "АМЕ_ВидыОпераций"

Returns results ranked by semantic similarity (score 0..1).
"""

if ENABLE_EMBEDDING:
    @mcp.tool(description=SEARCH_EMBEDDING_DESC)
    def search_by_embedding(query: str) -> str:
        """Semantic search by meaning — routines and metadata objects."""
        import json
        try:
            req = json.loads(query)
        except json.JSONDecodeError:
            return json.dumps({"error": "Invalid JSON"}, ensure_ascii=False)
        op = req.get("op", "")
        # Route description search to fulltext handler
        if op == "search_metadata_by_description":
            return handle_search_description(query)
        return handle_search_embedding(query)


def _load_data_background():
    """Load data in background thread so MCP server starts immediately."""
    try:
        load_all()
    except Exception:
        log.exception("Data load failed")


if __name__ == "__main__":
    log.info("Starting 1C Metacode MCP Lite — project: %s", PROJECT_NAME)

    # Load data in background
    loader_thread = threading.Thread(target=_load_data_background, daemon=True)
    loader_thread.start()

    # Start MCP server
    log.info("Starting MCP server on 0.0.0.0:%d", MCP_PORT)
    mcp.run(transport="sse", host="0.0.0.0", port=MCP_PORT)
