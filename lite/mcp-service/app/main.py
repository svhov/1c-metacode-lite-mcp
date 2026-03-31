"""1C Metacode MCP Server — lightweight edition.

Entry point: loads data into Memgraph, then starts FastMCP server with SSE transport.
"""

import logging
import sys
import threading

from fastmcp import FastMCP

from app.config import PROJECT_NAME, MCP_PORT
from app.db.loader import load_all
from app.db.connection import close as close_db
from app.tools.search_metadata import handle_search_metadata
from app.tools.search_code import handle_search_code
from app.tools.search_description import handle_search_description

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

mcp = FastMCP(f"1c-metacode-{PROJECT_NAME}")


SEARCH_METADATA_DESC = """Search 1C metadata graph. Send JSON with "op" field.

Operations: list_categories, list_objects_by_category, list_objects_by_name, object_structure,
list_attributes, list_resources, list_dimensions, list_characteristics,
list_tabular_parts, list_tabular_attributes, find_objects_with_tabular,
list_forms, list_form_controls, list_form_events,
list_commands, list_layouts,
list_modules_of_owner, list_module_routines, list_common_module_routines,
list_exported_routines, get_routine_body,
find_routines_by_name, find_routines_by_signature, find_unused_routines,
list_callers_of_routine, list_callees_of_routine,
list_enum_values, list_predefined_of_object,
list_roles_with_access_to_target, list_access_targets_of_role,
find_usages_of_object, find_objects_using_object,
resolve_qn, resolve_qn_prefix, find_by_guid, get_node_properties

Example: {"op": "list_objects_by_name", "name": "Файлы"}
Example: {"op": "find_routines_by_name", "name": "ПолучитьДанные"}
"""

SEARCH_CODE_DESC = """Search BSL code (1C procedures/functions).

Operations:
- find_routines_by_description: {"op": "find_routines_by_description", "text": "HTTP", "export": true}
- get_routine_body: {"op": "get_routine_body", "name": "ОтправитьЗапрос"} or {"op": "get_routine_body", "id": "..."}
"""

SEARCH_DESCRIPTION_DESC = """Search metadata objects by business description (Synonym, Comment, name).

Example: {"op": "search_metadata_by_description", "text": "документы предприятия"}
"""


@mcp.tool(description=SEARCH_METADATA_DESC)
def search_metadata(query: str) -> str:
    """Search 1C metadata graph by template operations."""
    return handle_search_metadata(query)


@mcp.tool(description=SEARCH_CODE_DESC)
def search_code(query: str) -> str:
    """Search BSL code — find routines or get routine body."""
    return handle_search_code(query)


@mcp.tool(description=SEARCH_DESCRIPTION_DESC)
def search_metadata_by_description(query: str) -> str:
    """Search metadata objects by description."""
    return handle_search_description(query)


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
