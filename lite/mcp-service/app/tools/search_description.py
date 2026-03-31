"""MCP tool: search_metadata_by_description — fulltext search on metadata objects."""

import json
import logging

from app.config import PROJECT_NAME
from app.db.connection import run_query

log = logging.getLogger(__name__)


def handle_search_description(query: str) -> str:
    """Search metadata objects by description/synonym/name."""
    try:
        req = json.loads(query)
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid JSON"}, ensure_ascii=False)

    op = req.get("op", "search_metadata_by_description")
    text = req.get("text", req.get("description", req.get("query", "")))
    category = req.get("category", "")
    limit = req.get("limit", 20)

    if not text:
        return json.dumps({"error": "Provide 'text' to search"}, ensure_ascii=False)

    try:
        conditions = ["mo.project_name = $p"]
        params = {"p": PROJECT_NAME, "text": text, "limit": limit}

        # Case-insensitive search: Memgraph toLower() doesn't handle Cyrillic,
        # so we search with multiple case variants from Python
        text_lower = text.lower()
        text_title = text.capitalize()
        text_upper = text.upper()
        params["t1"] = text
        params["t2"] = text_lower
        params["t3"] = text_title
        params["t4"] = text_upper
        conditions.append(
            "(mo.name CONTAINS $t1 OR mo.name CONTAINS $t2 "
            "OR mo.name CONTAINS $t3 OR mo.name CONTAINS $t4 "
            "OR (mo.Synonym IS NOT NULL AND (mo.Synonym CONTAINS $t1 OR mo.Synonym CONTAINS $t2 "
            "OR mo.Synonym CONTAINS $t3)) "
            "OR (mo.Comment IS NOT NULL AND (mo.Comment CONTAINS $t1 OR mo.Comment CONTAINS $t2 "
            "OR mo.Comment CONTAINS $t3)))"
        )

        if category:
            conditions.append("mo.category_name = $cat")
            params["cat"] = category

        where = " AND ".join(conditions)
        rows = run_query(f"""
            MATCH (mo:MetadataObject)
            WHERE {where}
            RETURN mo.name AS name, mo.category_name AS category,
                   mo.Synonym AS synonym, mo.Comment AS comment,
                   mo.qualified_name AS qn
            ORDER BY mo.name
            LIMIT $limit
        """, params)

        return json.dumps({"objects": rows}, ensure_ascii=False, default=str)
    except Exception as e:
        log.exception("Error in search_description")
        return json.dumps({"error": str(e)}, ensure_ascii=False)
