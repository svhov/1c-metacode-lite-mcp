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
        # so we search with multiple case variants from Python.
        # Also try stem prefix (drop last 1-2 chars) for basic morphology:
        # "премия" -> also try "преми" to match "Премии", "Премирование"
        variants = {text, text.lower(), text.capitalize(), text.upper()}
        if len(text) >= 4:
            variants.add(text[:-1])            # drop last char
            variants.add(text[:-1].capitalize())
            if len(text) >= 5:
                variants.add(text[:-2])        # drop last 2 chars
                variants.add(text[:-2].capitalize())
        variant_list = list(variants)
        params["variants"] = variant_list
        # Build OR conditions for each variant
        name_conds = " OR ".join(f"mo.name CONTAINS v" for _ in variant_list)
        syn_conds = " OR ".join(f"mo.Synonym CONTAINS v" for _ in variant_list)
        cmt_conds = " OR ".join(f"mo.Comment CONTAINS v" for _ in variant_list)
        # Use UNWIND + ANY pattern for Memgraph compatibility
        conditions.append(
            "ANY(v IN $variants WHERE mo.name CONTAINS v "
            "OR (mo.Synonym IS NOT NULL AND mo.Synonym CONTAINS v) "
            "OR (mo.Comment IS NOT NULL AND mo.Comment CONTAINS v))"
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
