"""MCP tool: search_metadata — template-based metadata queries.

Accepts JSON with "op" field routing to specific Cypher queries.
14 consolidated operations (with legacy aliases for backward compatibility).
"""

import json
import logging

from app.config import PROJECT_NAME
from app.db.connection import run_query

log = logging.getLogger(__name__)


def _get_name(req):
    """Extract object name from request, supporting multiple key variants."""
    for key in ("name", "object", "object_name", "owner", "module",
                 "enum", "enum_name", "register", "catalog", "document",
                 "role", "target", "service"):
        val = req.get(key, "")
        if val and val.strip():
            return val.strip()
    return ""


def handle_search_metadata(query: str) -> str:
    """Route query to appropriate handler based on 'op' field."""
    try:
        req = json.loads(query)
    except json.JSONDecodeError:
        return json.dumps({"error": "Invalid JSON"}, ensure_ascii=False)

    op = req.get("op", "")
    handler = HANDLERS.get(op)
    if not handler:
        primary_ops = [
            "browse", "object_structure", "get_children", "find_by_child",
            "get_form", "get_routines", "get_routine_body", "get_call_graph",
            "get_predefined", "get_access", "get_references",
            "get_subscriptions", "get_http_service", "resolve",
        ]
        return json.dumps({
            "error": f"Unknown operation: {op}",
            "available_operations": primary_ops,
        }, ensure_ascii=False)

    # Apply legacy defaults: auto-fill params for backward compatibility
    defaults = _LEGACY_DEFAULTS.get(op)
    if defaults:
        for key, val in defaults.items():
            if key not in req:
                req[key] = val

    try:
        result = handler(req)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        log.exception("Error in op=%s", op)
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Priority order when multiple objects share the same name
_CAT_PRIORITY = (
    "Справочники", "Документы", "РегистрыСведений", "РегистрыНакопления",
    "Обработки", "Перечисления", "Отчеты", "ОбщиеМодули",
)


def _find_object_qn(name: str, category: str = "") -> str | None:
    """Find MetadataObject by name or qn and return its qualified_name."""
    if not name:
        return None

    if "." in name and "/" not in name:
        parts = name.split(".", 1)
        cat_from_name, obj_name = parts[0].strip(), parts[1].strip()
        rows = run_query(
            "MATCH (mo:MetadataObject) WHERE mo.project_name = $p AND mo.name = $name "
            "AND mo.category_name = $cat RETURN mo.qualified_name AS qn LIMIT 1",
            {"p": PROJECT_NAME, "name": obj_name, "cat": cat_from_name},
        )
        if rows:
            return rows[0]["qn"]
        name = obj_name

    if category:
        rows = run_query(
            "MATCH (mo:MetadataObject) WHERE mo.project_name = $p AND mo.name = $name "
            "AND mo.category_name = $cat RETURN mo.qualified_name AS qn LIMIT 1",
            {"p": PROJECT_NAME, "name": name, "cat": category},
        )
        if rows:
            return rows[0]["qn"]

    if "/" in name:
        rows = run_query(
            "MATCH (mo:MetadataObject) WHERE mo.qualified_name = $name "
            "RETURN mo.qualified_name AS qn LIMIT 1",
            {"name": name},
        )
        if rows:
            return rows[0]["qn"]

    rows = run_query(
        "MATCH (mo:MetadataObject) WHERE mo.project_name = $p AND mo.name = $name "
        "RETURN mo.qualified_name AS qn, mo.category_name AS cat",
        {"p": PROJECT_NAME, "name": name},
    )
    if rows:
        if len(rows) == 1:
            return rows[0]["qn"]
        # Multiple matches — pick by category priority
        for cat in _CAT_PRIORITY:
            for r in rows:
                if r["cat"] == cat:
                    return r["qn"]
        return rows[0]["qn"]

    rows = run_query(
        "MATCH (mo:MetadataObject) WHERE mo.project_name = $p AND mo.name CONTAINS $name "
        "RETURN mo.qualified_name AS qn, mo.category_name AS cat",
        {"p": PROJECT_NAME, "name": name},
    )
    if rows:
        if len(rows) == 1:
            return rows[0]["qn"]
        for cat in _CAT_PRIORITY:
            for r in rows:
                if r["cat"] == cat:
                    return r["qn"]
        return rows[0]["qn"]
    return None


def _find_qn(req):
    return _find_object_qn(_get_name(req), req.get("category", ""))


# ---------------------------------------------------------------------------
# 1. browse — list categories, objects by category, objects by name
# ---------------------------------------------------------------------------

def _browse(req):
    category = req.get("category", "")
    name = _get_name(req)

    if not category and not name:
        rows = run_query(
            "MATCH (mc:MetadataCategory) WHERE mc.project_name = $p "
            "RETURN mc.name AS name, mc.qualified_name AS qn ORDER BY mc.name",
            {"p": PROJECT_NAME},
        )
        return {"categories": rows}

    if category and not name:
        rows = run_query(
            "MATCH (mc:MetadataCategory)-[:CONTAINS_OBJECT]->(mo) "
            "WHERE mc.project_name = $p AND mc.name = $cat "
            "RETURN mo.name AS name, mo.Synonym AS synonym, mo.qualified_name AS qn "
            "ORDER BY mo.name",
            {"p": PROJECT_NAME, "cat": category},
        )
        return {"objects": rows}

    conditions = ["mo.project_name = $p", "mo.name CONTAINS $name"]
    params = {"p": PROJECT_NAME, "name": name}
    if category:
        conditions.append("mo.category_name = $cat")
        params["cat"] = category

    rows = run_query(
        f"MATCH (mo:MetadataObject) WHERE {' AND '.join(conditions)} "
        "RETURN mo.name AS name, mo.category_name AS category, "
        "mo.Synonym AS synonym, mo.qualified_name AS qn "
        "ORDER BY mo.name LIMIT 50",
        params,
    )
    return {"objects": rows}


# ---------------------------------------------------------------------------
# 2. object_structure — full object overview
# ---------------------------------------------------------------------------

def _object_structure(req):
    name = _get_name(req)
    category = req.get("category", "")
    qn = _find_object_qn(name, category)
    if not qn:
        return {"error": f"Object not found: {name}"}

    obj = run_query(
        "MATCH (mo:MetadataObject) WHERE mo.qualified_name = $qn "
        "RETURN mo.qualified_name AS qn, mo.name AS name, "
        "mo.category_name AS category, mo.Synonym AS synonym, mo.Comment AS comment",
        {"qn": qn},
    )

    children = run_query(
        "MATCH (mo:MetadataObject)-[r]->(child) WHERE mo.qualified_name = $qn "
        "RETURN type(r) AS rel, labels(child)[0] AS label, "
        "child.name AS name, child.qualified_name AS child_qn "
        "ORDER BY type(r), child.name",
        {"qn": qn},
    )

    used_by = run_query(
        "MATCH (other:MetadataObject)-[:USED_IN]->(mo:MetadataObject) "
        "WHERE mo.qualified_name = $qn "
        "RETURN other.name AS name, other.category_name AS category",
        {"qn": qn},
    )

    uses = run_query(
        "MATCH (mo:MetadataObject)-[:USED_IN]->(target:MetadataObject) "
        "WHERE mo.qualified_name = $qn "
        "RETURN target.name AS name, target.category_name AS category",
        {"qn": qn},
    )

    result = {"object": obj[0] if obj else {}, "children": children}
    if used_by:
        result["referenced_by"] = used_by
    if uses:
        result["references"] = uses
    return result


# ---------------------------------------------------------------------------
# 3. get_children — attributes, resources, dimensions, tabular parts, etc.
# ---------------------------------------------------------------------------

_CHILD_TYPE_MAP = {
    "Attribute": ("HAS_ATTRIBUTE", "Attribute"),
    "Resource": ("HAS_RESOURCE", "Resource"),
    "Dimension": ("HAS_DIMENSION", "Dimension"),
    "Characteristic": ("HAS_CHARACTERISTIC", "Characteristic"),
    "TabularPart": ("HAS_TABULAR_PART", "TabularPart"),
    "EnumValue": ("HAS_ENUM_VALUE", "EnumValue"),
    "Command": ("HAS_COMMAND", "Command"),
    "Layout": ("HAS_LAYOUT", "Layout"),
}

# Register categories for auto-detecting register objects
_REGISTER_CATEGORIES = (
    "РегистрыСведений", "РегистрыНакопления", "РегистрыБухгалтерии", "РегистрыРасчета",
)


def _get_children(req):
    name = _get_name(req)
    child_type = req.get("child_type", "")
    tabular = req.get("tabular", req.get("tabular_part", ""))
    category = req.get("category", "")

    # For Resource/Dimension, auto-detect register categories
    if child_type in ("Resource", "Dimension") and not category:
        qn = None
        for reg_cat in _REGISTER_CATEGORIES:
            qn = _find_object_qn(name, reg_cat)
            if qn:
                break
        if not qn:
            qn = _find_object_qn(name, category)
    else:
        qn = _find_object_qn(name, category)

    if not qn:
        return {"children": [], "note": f"Object not found: {name}"}

    # TabularPart with specific tabular name → return its attributes
    if child_type == "TabularPart" and tabular:
        rows = run_query(
            "MATCH (mo:MetadataObject)-[:HAS_TABULAR_PART]->(tp:TabularPart)"
            "-[:HAS_ATTRIBUTE]->(a:Attribute) "
            "WHERE mo.qualified_name = $qn AND tp.name = $tab "
            "RETURN a.name AS name, a.type_info AS type ORDER BY a.name",
            {"qn": qn, "tab": tabular},
        )
        return {"tabular_attributes": rows, "tabular": tabular}

    # Specific child type
    if child_type and child_type in _CHILD_TYPE_MAP:
        rel, label = _CHILD_TYPE_MAP[child_type]
        rows = run_query(
            f"MATCH (mo:MetadataObject)-[:{rel}]->(c:{label}) "
            "WHERE mo.qualified_name = $qn "
            "RETURN c.name AS name, c.type_info AS type, c.Synonym AS synonym, "
            "c.qualified_name AS qn ORDER BY c.name",
            {"qn": qn},
        )
        return {"children": rows, "child_type": child_type}

    # All children
    rows = run_query(
        "MATCH (mo:MetadataObject)-[r]->(child) WHERE mo.qualified_name = $qn "
        "RETURN type(r) AS rel, labels(child)[0] AS label, "
        "child.name AS name, child.type_info AS type, child.Synonym AS synonym "
        "ORDER BY type(r), child.name",
        {"qn": qn},
    )
    return {"children": rows}


# ---------------------------------------------------------------------------
# 4. find_by_child — find objects with specific tabular/attribute
# ---------------------------------------------------------------------------

def _find_by_child(req):
    tabular = req.get("tabular", req.get("tabular_part", ""))
    attribute = req.get("attribute", "")

    if attribute and tabular:
        # Attribute within a specific tabular part
        rows = run_query(
            "MATCH (mo:MetadataObject)-[:HAS_TABULAR_PART]->(tp:TabularPart)"
            "-[:HAS_ATTRIBUTE]->(a:Attribute) "
            "WHERE mo.project_name = $p AND a.name CONTAINS $attr "
            "AND tp.name CONTAINS $tab "
            "RETURN mo.name AS object_name, tp.name AS tabular_name, "
            "a.name AS attribute_name ORDER BY mo.name",
            {"p": PROJECT_NAME, "attr": attribute, "tab": tabular},
        )
    elif attribute:
        # Search attribute in both direct children AND tabular parts
        direct = run_query(
            "MATCH (mo:MetadataObject)-[:HAS_ATTRIBUTE]->(a:Attribute) "
            "WHERE mo.project_name = $p AND a.name CONTAINS $attr "
            "RETURN mo.name AS object_name, mo.category_name AS category, "
            "'(реквизит)' AS context, a.name AS attribute_name ORDER BY mo.name",
            {"p": PROJECT_NAME, "attr": attribute},
        )
        in_tabular = run_query(
            "MATCH (mo:MetadataObject)-[:HAS_TABULAR_PART]->(tp:TabularPart)"
            "-[:HAS_ATTRIBUTE]->(a:Attribute) "
            "WHERE mo.project_name = $p AND a.name CONTAINS $attr "
            "RETURN mo.name AS object_name, mo.category_name AS category, "
            "tp.name AS context, a.name AS attribute_name ORDER BY mo.name",
            {"p": PROJECT_NAME, "attr": attribute},
        )
        rows = direct + in_tabular
    elif tabular:
        rows = run_query(
            "MATCH (mo:MetadataObject)-[:HAS_TABULAR_PART]->(tp:TabularPart) "
            "WHERE mo.project_name = $p AND tp.name CONTAINS $tab "
            "RETURN mo.name AS object_name, mo.category_name AS category, "
            "tp.name AS tabular_name ORDER BY mo.name",
            {"p": PROJECT_NAME, "tab": tabular},
        )
    else:
        return {"error": "Provide 'tabular' or 'attribute'"}

    return {"objects": rows}


# ---------------------------------------------------------------------------
# 5. get_form — forms, controls, events, bindings
# ---------------------------------------------------------------------------

def _get_form(req):
    name = _get_name(req)
    form_name = req.get("form", "")
    detail = req.get("detail", "")
    limit = int(req.get("limit", 200))

    # No form specified → list forms
    if not form_name and not detail:
        qn = _find_qn(req)
        if not qn:
            return {"forms": []}
        rows = run_query(
            "MATCH (mo:MetadataObject)-[:HAS_FORM]->(f:Form) "
            "WHERE mo.qualified_name = $qn "
            "RETURN f.name AS name, f.qualified_name AS qn ORDER BY f.name",
            {"qn": qn},
        )
        # Also include default forms info
        obj = run_query(
            "MATCH (mo:MetadataObject) WHERE mo.qualified_name = $qn "
            "RETURN properties(mo) AS props",
            {"qn": qn},
        )
        result = {"forms": rows}
        if obj:
            props = obj[0]["props"]
            defaults = {k: props[k] for k in props
                       if ("Форма" in k or "Form" in k) and props[k]}
            if defaults:
                result["default_forms"] = defaults
        return result

    # Specific form detail
    search_key = form_name or name

    if detail == "controls" or detail == "all":
        controls = run_query(
            f"MATCH (fc:FormControl) WHERE fc.qualified_name CONTAINS $name "
            f"RETURN fc.name AS name, fc.control_type AS type, "
            f"fc.data_path AS data_path ORDER BY fc.name LIMIT {limit}",
            {"name": search_key},
        )
    else:
        controls = None

    if detail == "events" or detail == "all":
        events = run_query(
            f"MATCH (fe:FormEvent) WHERE fe.qualified_name CONTAINS $name "
            f"RETURN fe.name AS event, fe.handler AS handler ORDER BY fe.name LIMIT {limit}",
            {"name": search_key},
        )
    else:
        events = None

    if detail == "attributes" or detail == "all":
        attributes = run_query(
            f"MATCH (fa:FormAttribute) WHERE fa.qualified_name CONTAINS $name "
            f"RETURN fa.name AS name, fa.qualified_name AS qn ORDER BY fa.name LIMIT {limit}",
            {"name": search_key},
        )
    else:
        attributes = None

    if detail == "commands" or detail == "all":
        commands = run_query(
            f"MATCH (fc:FormControl) WHERE fc.qualified_name CONTAINS $name "
            f"AND fc.control_type = 'UsualButton' "
            f"RETURN fc.name AS name, fc.qualified_name AS qn ORDER BY fc.name LIMIT {limit}",
            {"name": search_key},
        )
    else:
        commands = None

    if detail == "bindings" or detail == "all":
        bindings = run_query(
            f"MATCH (fc:FormControl)-[:BINDS_TO]->(fa:FormAttribute) "
            f"WHERE fc.qualified_name CONTAINS $name "
            f"RETURN fc.name AS control, fa.name AS attribute ORDER BY fc.name LIMIT {limit}",
            {"name": search_key},
        )
    else:
        bindings = None

    result = {}
    if controls is not None:
        result["controls"] = controls
    if events is not None:
        result["events"] = events
    if attributes is not None:
        result["attributes"] = attributes
    if commands is not None:
        result["commands"] = commands
    if bindings is not None:
        result["bindings"] = bindings
    return result


# ---------------------------------------------------------------------------
# 6. get_routines — modules, routines, search, filter
# ---------------------------------------------------------------------------

def _get_routines(req):
    name = _get_name(req)
    module = req.get("module", "")
    signature = req.get("signature", "")
    export_only = req.get("export", None)
    unused = req.get("unused", False)

    # Find unused routines
    if unused:
        rows = run_query(
            "MATCH (r:Routine) WHERE r.project_name = $p AND r.export = false "
            "AND NOT EXISTS { MATCH ()-[:CALLS]->(r) } "
            "RETURN r.name AS name, r.owner_qn AS owner, r.id AS id "
            "ORDER BY r.name LIMIT 100",
            {"p": PROJECT_NAME},
        )
        return {"routines": rows}

    # Search by signature
    if signature:
        rows = run_query(
            "MATCH (r:Routine) WHERE r.project_name = $p AND r.signature CONTAINS $sig "
            "RETURN r.name AS name, r.signature AS signature, r.id AS id, "
            "r.owner_qn AS owner ORDER BY r.name LIMIT 50",
            {"p": PROJECT_NAME, "sig": signature},
        )
        return {"routines": rows}

    # Build conditions
    conditions = ["r.project_name = $p"]
    params = {"p": PROJECT_NAME}

    if module:
        conditions.append("r.owner_qn CONTAINS $module")
        params["module"] = module
    if name:
        # Could be object name (owner) or routine name — try both
        conditions.append("(r.name CONTAINS $name OR r.owner_qn CONTAINS $name)")
        params["name"] = name
    if export_only is True:
        conditions.append("r.export = true")

    where = " AND ".join(conditions)
    rows = run_query(
        f"MATCH (r:Routine) WHERE {where} "
        "RETURN r.name AS name, r.routine_type AS type, r.export AS export, "
        "r.directive AS directive, r.signature AS signature, "
        "r.id AS id, r.owner_qn AS owner "
        "ORDER BY r.name LIMIT 50",
        params,
    )
    return {"routines": rows}


# ---------------------------------------------------------------------------
# 7. get_routine_body — get full source code of a routine
# ---------------------------------------------------------------------------

def _get_routine_body(req):
    routine_id = req.get("id", req.get("routine_id", ""))
    name = req.get("name", "")
    owner = req.get("owner", "")

    if routine_id:
        rows = run_query(
            "MATCH (r:Routine) WHERE r.id = $id "
            "RETURN r.name AS name, r.body AS body, r.signature AS signature, "
            "r.owner_qn AS owner, r.doc_description AS doc, "
            "r.line AS line, r.file_path AS file_path",
            {"id": routine_id},
        )
    elif name and owner:
        rows = run_query(
            "MATCH (r:Routine) WHERE r.project_name = $p AND r.name = $name "
            "AND (r.owner_qn CONTAINS $owner OR r.id CONTAINS $owner) "
            "RETURN r.name AS name, r.body AS body, r.signature AS signature, "
            "r.owner_qn AS owner, r.doc_description AS doc, "
            "r.line AS line, r.file_path AS file_path LIMIT 1",
            {"p": PROJECT_NAME, "name": name, "owner": owner},
        )
        if not rows:
            # Fallback: search without owner filter, just by name
            rows = run_query(
                "MATCH (r:Routine) WHERE r.project_name = $p AND r.name = $name "
                "RETURN r.name AS name, r.body AS body, r.signature AS signature, "
                "r.owner_qn AS owner, r.doc_description AS doc, "
                "r.line AS line, r.file_path AS file_path LIMIT 1",
                {"p": PROJECT_NAME, "name": name},
            )
    elif name:
        rows = run_query(
            "MATCH (r:Routine) WHERE r.project_name = $p AND r.name = $name "
            "RETURN r.name AS name, r.body AS body, r.signature AS signature, "
            "r.owner_qn AS owner, r.doc_description AS doc, "
            "r.line AS line, r.file_path AS file_path LIMIT 1",
            {"p": PROJECT_NAME, "name": name},
        )
    else:
        return {"error": "Provide 'id' or 'name'"}

    if rows:
        return rows[0]
    return {"error": f"Routine not found: {name or routine_id}"}


# ---------------------------------------------------------------------------
# 8. get_call_graph — callers, callees, tree, cross-owner calls
# ---------------------------------------------------------------------------

def _get_call_graph(req):
    name = req.get("name", "")
    routine_id = req.get("id", req.get("routine_id", ""))
    direction = req.get("direction", "callees")
    depth = req.get("depth", 3)
    owner_from = req.get("from", req.get("owner1", ""))
    owner_to = req.get("to", req.get("owner2", ""))

    # Cross-owner calls
    if owner_from and owner_to:
        rows = run_query(
            "MATCH (r1:Routine)-[:CALLS]->(r2:Routine) "
            "WHERE r1.owner_qn CONTAINS $o1 AND r2.owner_qn CONTAINS $o2 "
            "RETURN r1.name AS caller, r1.owner_qn AS caller_owner, "
            "r2.name AS callee, r2.owner_qn AS callee_owner",
            {"o1": owner_from, "o2": owner_to},
        )
        return {"calls": rows}

    # Resolve routine
    if routine_id:
        id_cond = "r.id = $id"
        params = {"id": routine_id}
    elif name:
        id_cond = "r.project_name = $p AND r.name = $name"
        params = {"p": PROJECT_NAME, "name": name}
    else:
        return {"error": "Provide 'id', 'name', or 'from'/'to'"}

    if direction == "callers":
        rows = run_query(
            f"MATCH (caller:Routine)-[:CALLS]->(r:Routine) WHERE {id_cond} "
            "RETURN caller.name AS name, caller.owner_qn AS owner, caller.id AS id",
            params,
        )
        return {"callers": rows}

    elif direction == "tree":
        rows = run_query(
            f"MATCH path = (r:Routine)-[:CALLS*1..3]->(callee:Routine) "
            f"WHERE {id_cond} "
            "RETURN callee.name AS name, callee.owner_qn AS owner, callee.id AS id",
            params,
        )
        return {"call_tree": rows}

    else:  # callees
        rows = run_query(
            f"MATCH (r:Routine)-[:CALLS]->(callee:Routine) WHERE {id_cond} "
            "RETURN callee.name AS name, callee.owner_qn AS owner, callee.id AS id",
            params,
        )
        return {"callees": rows}


# ---------------------------------------------------------------------------
# 9. get_predefined — predefined items of an object
# ---------------------------------------------------------------------------

def _get_predefined(req):
    name = _get_name(req)
    item = req.get("item", req.get("predefined", ""))
    is_folder = req.get("is_folder", None)

    qn = _find_qn(req)
    if not qn:
        if is_folder is not None:
            flag = str(is_folder).lower() in ("true", "1", "yes")
            rows = run_query(
                "MATCH (mo:MetadataObject)-[:HAS_PREDEFINED]->(pi:PredefinedItem) "
                "WHERE mo.project_name = $p AND pi.is_folder = $flag "
                "RETURN mo.name AS object, pi.name AS name, pi.description AS description",
                {"p": PROJECT_NAME, "flag": flag},
            )
            return {"predefined": rows}
        return {"predefined": [], "note": f"Object not found: {name}"}

    conditions = ["mo.qualified_name = $qn"]
    params = {"qn": qn}

    if item:
        conditions.append("pi.name CONTAINS $item")
        params["item"] = item
    if is_folder is not None:
        flag = str(is_folder).lower() in ("true", "1", "yes")
        conditions.append("pi.is_folder = $flag")
        params["flag"] = flag

    where = " AND ".join(conditions)
    rows = run_query(
        f"MATCH (mo:MetadataObject)-[:HAS_PREDEFINED]->(pi:PredefinedItem) "
        f"WHERE {where} "
        "RETURN pi.name AS name, pi.description AS description, "
        "pi.is_folder AS is_folder ORDER BY pi.name",
        params,
    )
    return {"predefined": rows}


# ---------------------------------------------------------------------------
# 10. get_access — role access rights
# ---------------------------------------------------------------------------

def _get_access(req):
    role = req.get("role", "")
    target = req.get("target", "")

    if not role and not target:
        target = _get_name(req)

    # Specific role + target
    if role and target:
        rows = run_query(
            "MATCH (rn:MetadataObject)-[rel:GRANTS_ACCESS_TO]->(tn:MetadataObject) "
            "WHERE rn.project_name = $p AND rn.name = $role AND tn.name = $target "
            "RETURN rn.name AS role, tn.name AS target, rel.rights AS rights",
            {"p": PROJECT_NAME, "role": role, "target": target},
        )
        if rows:
            return rows[0]
        return {"error": "No access found"}

    # Target only → which roles have access
    if target:
        rows = run_query(
            "MATCH (rn:MetadataObject)-[rel:GRANTS_ACCESS_TO]->(tn:MetadataObject) "
            "WHERE tn.name = $target "
            "RETURN rn.name AS role, rel.rights AS rights ORDER BY rn.name",
            {"target": target},
        )
        if not rows:
            rows = run_query(
                "MATCH (rn:MetadataObject)-[rel:GRANTS_ACCESS_TO]->(tn:MetadataObject) "
                "WHERE tn.name CONTAINS $target "
                "RETURN rn.name AS role, tn.name AS target, rel.rights AS rights "
                "ORDER BY rn.name",
                {"target": target},
            )
        return {"roles": rows}

    # Role only → what can it access
    rows = run_query(
        "MATCH (rn:MetadataObject)-[rel:GRANTS_ACCESS_TO]->(tn:MetadataObject) "
        "WHERE rn.project_name = $p AND rn.name = $role "
        "RETURN tn.name AS target, tn.category_name AS category, "
        "rel.rights AS rights ORDER BY tn.name",
        {"p": PROJECT_NAME, "role": role},
    )
    return {"targets": rows}


# ---------------------------------------------------------------------------
# 11. get_references — USED_IN, MOVEMENTS_IN
# ---------------------------------------------------------------------------

def _get_references(req):
    name = _get_name(req)
    direction = req.get("direction", "incoming")

    if direction == "movements":
        rows = run_query(
            "MATCH (doc:MetadataObject)-[:MOVEMENTS_IN]->(reg:MetadataObject) "
            "WHERE reg.name CONTAINS $name AND reg.project_name = $p "
            "RETURN doc.name AS document, doc.category_name AS category, "
            "reg.name AS register",
            {"p": PROJECT_NAME, "name": name},
        )
        return {"documents": rows}

    qn = _find_qn(req)
    if not qn:
        return {"references": [], "note": f"Object not found: {name}"}

    if direction == "outgoing":
        rows = run_query(
            "MATCH (mo:MetadataObject)-[:USED_IN]->(target:MetadataObject) "
            "WHERE mo.qualified_name = $qn "
            "RETURN target.name AS name, target.category_name AS category, "
            "target.qualified_name AS qn ORDER BY target.name",
            {"qn": qn},
        )
        return {"uses": rows}

    # incoming (default)
    rows = run_query(
        "MATCH (other:MetadataObject)-[:USED_IN]->(mo:MetadataObject) "
        "WHERE mo.qualified_name = $qn "
        "RETURN other.name AS name, other.category_name AS category, "
        "other.qualified_name AS qn ORDER BY other.name",
        {"qn": qn},
    )
    return {"usages": rows}


# ---------------------------------------------------------------------------
# 12. get_subscriptions — event subscriptions
# ---------------------------------------------------------------------------

def _get_subscriptions(req):
    name = _get_name(req)
    if name:
        rows = run_query(
            "MATCH (es:EventSubscription) WHERE es.qualified_name CONTAINS $name "
            "RETURN es.name AS name, es.qualified_name AS qn ORDER BY es.name",
            {"name": name},
        )
    else:
        rows = run_query(
            "MATCH (es:EventSubscription) WHERE es.qualified_name STARTS WITH $prefix "
            "RETURN es.name AS name, es.qualified_name AS qn ORDER BY es.name",
            {"prefix": f"{PROJECT_NAME}/"},
        )
    return {"subscriptions": rows}


# ---------------------------------------------------------------------------
# 13. get_http_service — HTTP services, URL templates, methods
# ---------------------------------------------------------------------------

def _get_http_service(req):
    name = _get_name(req)
    template = req.get("template", "")

    # Template methods
    if template:
        rows = run_query(
            "MATCH (ut:UrlTemplate)-[:HAS_URL_METHOD]->(um:UrlMethod) "
            "WHERE ut.qualified_name CONTAINS $tpl "
            "RETURN um.name AS name, um.qualified_name AS qn ORDER BY um.name",
            {"tpl": template},
        )
        return {"methods": rows}

    # Service URL templates
    if name:
        qn = _find_qn(req)
        if qn:
            rows = run_query(
                "MATCH (mo:MetadataObject)-[:HAS_URL_TEMPLATE]->(ut:UrlTemplate) "
                "WHERE mo.qualified_name = $qn "
                "RETURN ut.name AS name, ut.qualified_name AS qn ORDER BY ut.name",
                {"qn": qn},
            )
            return {"templates": rows}

    # List all HTTP services
    rows = run_query(
        "MATCH (mc:MetadataCategory)-[:CONTAINS_OBJECT]->(mo:MetadataObject) "
        "WHERE mc.project_name = $p AND mc.name = 'HTTPСервисы' "
        "RETURN mo.name AS name, mo.qualified_name AS qn ORDER BY mo.name",
        {"p": PROJECT_NAME},
    )
    return {"http_services": rows}


# ---------------------------------------------------------------------------
# 14. resolve — resolve by qn, guid, prefix
# ---------------------------------------------------------------------------

def _resolve(req):
    qn = req.get("qn", req.get("qualified_name", ""))
    prefix = req.get("prefix", "")
    guid = req.get("guid", req.get("id", ""))

    if guid and not qn:
        rows = run_query(
            "MATCH (n) WHERE n.guid = $guid "
            "RETURN labels(n)[0] AS label, n.name AS name, n.qualified_name AS qn",
            {"guid": guid},
        )
        if rows:
            return rows[0]
        return {"error": f"GUID not found: {guid}"}

    if prefix:
        # If prefix looks like a qn path (contains /), search by qn prefix
        # Otherwise search by name prefix (e.g. "АМЕ_")
        if "/" in prefix:
            rows = run_query(
                "MATCH (n) WHERE n.qualified_name STARTS WITH $prefix "
                "RETURN labels(n)[0] AS label, n.name AS name, n.qualified_name AS qn "
                "ORDER BY n.qualified_name LIMIT 50",
                {"prefix": prefix},
            )
        else:
            rows = run_query(
                "MATCH (n) WHERE n.project_name = $p AND n.name STARTS WITH $prefix "
                "RETURN labels(n)[0] AS label, n.name AS name, n.qualified_name AS qn "
                "ORDER BY n.name LIMIT 50",
                {"prefix": prefix, "p": PROJECT_NAME},
            )
        return {"nodes": rows}

    if qn:
        rows = run_query(
            "MATCH (n) WHERE n.qualified_name = $qn "
            "RETURN labels(n)[0] AS label, n.name AS name, properties(n) AS props",
            {"qn": qn},
        )
        if rows:
            return rows[0]
        return {"error": f"Not found: {qn}"}

    # Fallback: get_node_properties by name
    name = _get_name(req)
    if name:
        rows = run_query(
            "MATCH (n) WHERE n.name = $name AND n.project_name = $p "
            "RETURN labels(n) AS labels, properties(n) AS props LIMIT 1",
            {"name": name, "p": PROJECT_NAME},
        )
        if rows:
            return rows[0]

    return {"error": "Provide 'qn', 'prefix', 'guid', or 'name'"}


# ---------------------------------------------------------------------------
# Legacy op → default params (auto-fill for backward compatibility)
# ---------------------------------------------------------------------------

_LEGACY_DEFAULTS = {
    # get_children aliases: auto-fill child_type
    "list_attributes": {"child_type": "Attribute"},
    "list_attributes_with_type": {"child_type": "Attribute"},
    "list_resources": {"child_type": "Resource"},
    "list_resources_with_type": {"child_type": "Resource"},
    "list_dimensions": {"child_type": "Dimension"},
    "list_dimensions_with_type": {"child_type": "Dimension"},
    "list_characteristics": {"child_type": "Characteristic"},
    "list_characteristics_with_type": {"child_type": "Characteristic"},
    "list_tabular_parts": {"child_type": "TabularPart"},
    "list_tabular_attributes": {"child_type": "TabularPart"},  # needs tabular param too
    "list_enum_values": {"child_type": "EnumValue"},
    "list_commands": {"child_type": "Command"},
    "list_layouts": {"child_type": "Layout"},
    # get_form aliases: auto-fill detail
    "list_form_controls": {"detail": "controls"},
    "list_form_events": {"detail": "events"},
    "list_form_event_handlers": {"detail": "events"},
    "list_form_attributes_of_form": {"detail": "attributes"},
    "list_form_commands": {"detail": "commands"},
    "list_form_bindings": {"detail": "bindings"},
    "find_controls_bound_to": {"detail": "bindings"},
    # get_call_graph aliases: auto-fill direction
    "list_callers_of_routine": {"direction": "callers"},
    "list_callees_of_routine": {"direction": "callees"},
    "call_graph_subtree": {"direction": "tree"},
    # get_routines aliases
    "list_exported_routines": {"export": True},
    "find_unused_routines": {"unused": True},
    # get_references aliases: auto-fill direction
    "find_usages_of_object": {"direction": "incoming"},
    "find_objects_using_object": {"direction": "outgoing"},
    "find_documents_making_movements_into_register": {"direction": "movements"},
}

# ---------------------------------------------------------------------------
# Handler registry — 14 primary ops + legacy aliases
# ---------------------------------------------------------------------------

HANDLERS = {
    # Primary operations (14)
    "browse": _browse,
    "object_structure": _object_structure,
    "get_children": _get_children,
    "find_by_child": _find_by_child,
    "get_form": _get_form,
    "get_routines": _get_routines,
    "get_routine_body": _get_routine_body,
    "get_call_graph": _get_call_graph,
    "get_predefined": _get_predefined,
    "get_access": _get_access,
    "get_references": _get_references,
    "get_subscriptions": _get_subscriptions,
    "get_http_service": _get_http_service,
    "resolve": _resolve,

    # Legacy aliases (backward compatibility)
    "list_categories": _browse,
    "list_objects_by_category": _browse,
    "list_objects_by_name": _browse,
    "list_attributes": _get_children,
    "list_attributes_with_type": _get_children,
    "list_resources": _get_children,
    "list_resources_with_type": _get_children,
    "list_dimensions": _get_children,
    "list_dimensions_with_type": _get_children,
    "list_characteristics": _get_children,
    "list_characteristics_with_type": _get_children,
    "list_tabular_parts": _get_children,
    "list_tabular_attributes": _get_children,
    "find_objects_with_tabular": _find_by_child,
    "find_objects_by_attribute_in_tabular": _find_by_child,
    "list_forms": _get_form,
    "list_form_controls": _get_form,
    "list_form_events": _get_form,
    "list_form_event_handlers": _get_form,
    "list_form_attributes_of_form": _get_form,
    "list_form_commands": _get_form,
    "list_form_bindings": _get_form,
    "find_controls_bound_to": _get_form,
    "get_default_forms": _get_form,
    "list_commands": _get_children,
    "list_layouts": _get_children,
    "list_modules_of_owner": _get_routines,
    "list_module_routines": _get_routines,
    "list_common_module_routines": _get_routines,
    "list_exported_routines": _get_routines,
    "find_routines_by_name": _get_routines,
    "find_routines_by_signature": _get_routines,
    "find_unused_routines": _get_routines,
    "list_callers_of_routine": _get_call_graph,
    "list_callees_of_routine": _get_call_graph,
    "call_graph_subtree": _get_call_graph,
    "find_calls_between_owners": _get_call_graph,
    "list_enum_values": _get_children,
    "list_predefined_of_object": _get_predefined,
    "find_predefined_by_name_in_object": _get_predefined,
    "find_predefined_by_flag": _get_predefined,
    "find_predefined_by_kind": _get_predefined,
    "find_predefined_by_account_type": _get_predefined,
    "list_roles_with_access_to_target": _get_access,
    "list_access_targets_of_role": _get_access,
    "get_access_of_role_to_target": _get_access,
    "list_event_subscriptions": _get_subscriptions,
    "list_event_subscriptions_of_object": _get_subscriptions,
    "list_http_services": _get_http_service,
    "list_url_templates_of_service": _get_http_service,
    "list_url_methods_of_template": _get_http_service,
    "find_usages_of_object": _get_references,
    "find_objects_using_object": _get_references,
    "find_documents_making_movements_into_register": _get_references,
    "find_journals_by_graph": _get_references,
    "resolve_qn": _resolve,
    "resolve_qn_prefix": _resolve,
    "find_by_guid": _resolve,
    "get_node_properties": _resolve,
}
