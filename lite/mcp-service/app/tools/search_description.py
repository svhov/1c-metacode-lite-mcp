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

    text = req.get("text", req.get("description", req.get("query", "")))
    category = req.get("category", "")
    limit = req.get("limit", 20)

    if not text:
        return json.dumps({"error": "Provide 'text' to search"}, ensure_ascii=False)

    try:
        conditions = ["mo.project_name = $p"]
        params = {"p": PROJECT_NAME, "limit": limit}

        # Split query into individual words for matching CamelCase names
        # "документы предприятия" → each word matches independently against
        # ДокументыПредприятия (because "документ" CONTAINS works on CamelCase)
        words = [w.strip() for w in text.split() if len(w.strip()) >= 3]
        if not words:
            words = [text]

        # Build variants for each word: original + stem (drop 1-2 chars for morphology)
        all_variants = set()
        for word in words:
            all_variants.add(word)
            all_variants.add(word.lower())
            all_variants.add(word.capitalize())
            if len(word) >= 4:
                all_variants.add(word[:-1])
                all_variants.add(word[:-1].capitalize())
                if len(word) >= 5:
                    all_variants.add(word[:-2])
                    all_variants.add(word[:-2].capitalize())

        variant_list = list(all_variants)
        params["variants"] = variant_list

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
