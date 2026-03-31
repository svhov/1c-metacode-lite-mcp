"""MCP tool: search_code — BSL code search."""

import json
import logging

from app.config import PROJECT_NAME
from app.db.connection import run_query

log = logging.getLogger(__name__)


def handle_search_code(query: str) -> str:
    """Search BSL code: find routines by description or get routine body."""
    try:
        req = json.loads(query)
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid JSON"}, ensure_ascii=False)

    op = req.get("op", "")

    try:
        if op == "find_routines_by_description":
            return json.dumps(_find_by_description(req), ensure_ascii=False, default=str)
        elif op == "get_routine_body":
            return json.dumps(_get_body(req), ensure_ascii=False, default=str)
        else:
            return json.dumps({
                "error": f"Unknown op: {op}",
                "available": ["find_routines_by_description", "get_routine_body"],
            }, ensure_ascii=False)
    except Exception as e:
        log.exception("Error in search_code op=%s", op)
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def _find_by_description(req):
    text = req.get("text", req.get("description", ""))
    owner = req.get("owner", "")
    routine_type = req.get("routine_type", "")
    export_only = req.get("export", None)
    limit = req.get("limit", 20)

    conditions = ["r.project_name = $p"]
    params = {"p": PROJECT_NAME, "text": text}

    if text:
        conditions.append("(r.doc_description CONTAINS $text OR r.name CONTAINS $text)")
    if owner:
        conditions.append("r.owner_qn CONTAINS $owner")
        params["owner"] = owner
    if routine_type:
        conditions.append("r.routine_type = $rtype")
        params["rtype"] = routine_type
    if export_only is True:
        conditions.append("r.export = true")

    where = " AND ".join(conditions)
    rows = run_query(f"""
        MATCH (r:Routine)
        WHERE {where}
        RETURN r.name AS name, r.signature AS signature,
               r.doc_description AS description,
               r.owner_qn AS owner, r.id AS id,
               r.export AS export, r.directive AS directive
        ORDER BY r.name
        LIMIT $limit
    """, {**params, "limit": limit})

    return {"routines": rows}


def _get_body(req):
    routine_id = req.get("id", req.get("routine_id", ""))
    name = req.get("name", "")

    if routine_id:
        rows = run_query("""
            MATCH (r:Routine {id: $id})
            RETURN r.name AS name, r.body AS body, r.signature AS signature,
                   r.owner_qn AS owner, r.doc_description AS doc,
                   r.line AS line, r.file_path AS file_path
        """, {"id": routine_id})
    elif name:
        rows = run_query("""
            MATCH (r:Routine {project_name: $p, name: $name})
            RETURN r.name AS name, r.body AS body, r.signature AS signature,
                   r.owner_qn AS owner, r.doc_description AS doc,
                   r.line AS line, r.file_path AS file_path
            LIMIT 1
        """, {"p": PROJECT_NAME, "name": name})
    else:
        return {"error": "Provide 'id' or 'name'"}

    if rows:
        return rows[0]
    return {"error": "Routine not found"}
