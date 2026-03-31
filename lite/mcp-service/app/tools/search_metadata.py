"""MCP tool: search_metadata — template-based metadata queries.

Accepts JSON with "op" field routing to specific Cypher queries.
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
        ops = sorted(HANDLERS.keys())
        return json.dumps({
            "error": f"Unknown operation: {op}",
            "available_operations": ops,
        }, ensure_ascii=False)

    try:
        result = handler(req)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        log.exception("Error in op=%s", op)
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def _find_object_qn(name: str, category: str = "") -> str | None:
    """Find MetadataObject by name or qn and return its qualified_name.

    Supports formats:
    - Short name: "ДокументыПредприятия"
    - Category.Name: "Справочники.ДокументыПредприятия" or "РегистрыСведений.АМЕ_АрхивДокументов"
    - Full qn: "do_ame/АМЕ/Справочники/ДокументыПредприятия"
    - Category param: category="РегистрыСведений", name="АМЕ_АрхивДокументов"
    """
    if not name:
        return None

    # Parse "Категория.Имя" format
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
        # Fallback: treat whole string as name
        name = obj_name

    # If category specified, use it for precise match
    if category:
        rows = run_query(
            "MATCH (mo:MetadataObject) WHERE mo.project_name = $p AND mo.name = $name "
            "AND mo.category_name = $cat RETURN mo.qualified_name AS qn LIMIT 1",
            {"p": PROJECT_NAME, "name": name, "cat": category},
        )
        if rows:
            return rows[0]["qn"]

    # Try as full qualified_name
    if "/" in name:
        rows = run_query(
            "MATCH (mo:MetadataObject) WHERE mo.qualified_name = $name "
            "RETURN mo.qualified_name AS qn LIMIT 1",
            {"name": name},
        )
        if rows:
            return rows[0]["qn"]

    # Try exact match by name
    rows = run_query(
        "MATCH (mo:MetadataObject) WHERE mo.project_name = $p AND mo.name = $name "
        "RETURN mo.qualified_name AS qn LIMIT 1",
        {"p": PROJECT_NAME, "name": name},
    )
    if rows:
        return rows[0]["qn"]

    # Try CONTAINS
    rows = run_query(
        "MATCH (mo:MetadataObject) WHERE mo.project_name = $p AND mo.name CONTAINS $name "
        "RETURN mo.qualified_name AS qn LIMIT 1",
        {"p": PROJECT_NAME, "name": name},
    )
    if rows:
        return rows[0]["qn"]
    return None


# --- Handlers ---

def _list_categories(req):
    rows = run_query(
        "MATCH (mc:MetadataCategory) WHERE mc.project_name = $p "
        "RETURN mc.name AS name, mc.qualified_name AS qn ORDER BY mc.name",
        {"p": PROJECT_NAME},
    )
    return {"categories": rows}


def _list_objects_by_category(req):
    category = req.get("category", "")
    rows = run_query(
        "MATCH (mc:MetadataCategory)-[:CONTAINS_OBJECT]->(mo) "
        "WHERE mc.project_name = $p AND mc.name = $cat "
        "RETURN mo.name AS name, mo.Synonym AS synonym, mo.qualified_name AS qn "
        "ORDER BY mo.name",
        {"p": PROJECT_NAME, "cat": category},
    )
    return {"objects": rows}


def _list_objects_by_name(req):
    name = _get_name(req)
    rows = run_query(
        "MATCH (mo:MetadataObject) WHERE mo.project_name = $p AND mo.name CONTAINS $name "
        "RETURN mo.name AS name, mo.category_name AS category, "
        "mo.Synonym AS synonym, mo.qualified_name AS qn "
        "ORDER BY mo.name LIMIT 50",
        {"p": PROJECT_NAME, "name": name},
    )
    return {"objects": rows}


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

    # Add USED_IN: objects that reference this one
    used_by = run_query(
        "MATCH (other:MetadataObject)-[:USED_IN]->(mo:MetadataObject) "
        "WHERE mo.qualified_name = $qn "
        "RETURN other.name AS name, other.category_name AS category",
        {"qn": qn},
    )

    # Add USED_IN: objects this one references
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


def _find_qn(req):
    """Helper: extract name+category from request and resolve to qn."""
    return _find_object_qn(_get_name(req), req.get("category", ""))


def _list_attributes(req):
    name = _get_name(req)
    qn = _find_qn(req)
    if not qn:
        return {"attributes": [], "note": f"Object not found: {name}"}
    rows = run_query(
        "MATCH (mo:MetadataObject)-[:HAS_ATTRIBUTE]->(a:Attribute) "
        "WHERE mo.qualified_name = $qn "
        "RETURN a.name AS name, a.type_info AS type, a.Synonym AS synonym "
        "ORDER BY a.name",
        {"qn": qn},
    )
    return {"attributes": rows}


def _list_attributes_with_type(req):
    return _list_attributes(req)


def _list_resources(req):
    name = _get_name(req)
    category = req.get("category", "")
    # Resources belong to registers — prefer register categories if no category given
    if not category:
        for reg_cat in ("РегистрыСведений", "РегистрыНакопления", "РегистрыБухгалтерии", "РегистрыРасчета"):
            qn = _find_object_qn(name, reg_cat)
            if qn:
                break
        else:
            qn = _find_qn(req)
    else:
        qn = _find_qn(req)
    if not qn:
        return {"resources": [], "note": f"Object not found: {name}"}
    rows = run_query(
        "MATCH (mo:MetadataObject)-[:HAS_RESOURCE]->(r:Resource) "
        "WHERE mo.qualified_name = $qn "
        "RETURN r.name AS name, r.type_info AS type, r.Synonym AS synonym "
        "ORDER BY r.name",
        {"qn": qn},
    )
    return {"resources": rows}


def _list_resources_with_type(req):
    return _list_resources(req)


def _list_dimensions(req):
    name = _get_name(req)
    category = req.get("category", "")
    # Dimensions belong to registers — prefer register categories if no category given
    if not category:
        for reg_cat in ("РегистрыСведений", "РегистрыНакопления", "РегистрыБухгалтерии", "РегистрыРасчета"):
            qn = _find_object_qn(name, reg_cat)
            if qn:
                break
        else:
            qn = _find_qn(req)
    else:
        qn = _find_qn(req)
    if not qn:
        return {"dimensions": [], "note": f"Object not found: {name}"}
    rows = run_query(
        "MATCH (mo:MetadataObject)-[:HAS_DIMENSION]->(d:Dimension) "
        "WHERE mo.qualified_name = $qn "
        "RETURN d.name AS name, d.type_info AS type, d.Synonym AS synonym "
        "ORDER BY d.name",
        {"qn": qn},
    )
    return {"dimensions": rows}


def _list_dimensions_with_type(req):
    return _list_dimensions(req)


def _list_characteristics(req):
    name = _get_name(req)
    qn = _find_qn(req)
    if not qn:
        return {"characteristics": []}
    rows = run_query(
        "MATCH (mo:MetadataObject)-[:HAS_CHARACTERISTIC]->(c:Characteristic) "
        "WHERE mo.qualified_name = $qn "
        "RETURN c.name AS name, c.type_info AS type ORDER BY c.name",
        {"qn": qn},
    )
    return {"characteristics": rows}


def _list_characteristics_with_type(req):
    return _list_characteristics(req)


def _list_tabular_parts(req):
    name = _get_name(req)
    qn = _find_qn(req)
    if not qn:
        return {"tabular_parts": []}
    rows = run_query(
        "MATCH (mo:MetadataObject)-[:HAS_TABULAR_PART]->(tp:TabularPart) "
        "WHERE mo.qualified_name = $qn "
        "RETURN tp.name AS name, tp.qualified_name AS qn ORDER BY tp.name",
        {"qn": qn},
    )
    return {"tabular_parts": rows}


def _list_tabular_attributes(req):
    name = _get_name(req)
    tabular = req.get("tabular", req.get("tabular_part", ""))
    qn = _find_qn(req)
    if not qn:
        return {"attributes": []}
    rows = run_query(
        "MATCH (mo:MetadataObject)-[:HAS_TABULAR_PART]->(tp:TabularPart)-[:HAS_ATTRIBUTE]->(a:Attribute) "
        "WHERE mo.qualified_name = $qn AND tp.name = $tab "
        "RETURN a.name AS name, a.type_info AS type ORDER BY a.name",
        {"qn": qn, "tab": tabular},
    )
    return {"attributes": rows}


def _find_objects_with_tabular(req):
    tabular = req.get("tabular", req.get("tabular_part", ""))
    rows = run_query(
        "MATCH (mo:MetadataObject)-[:HAS_TABULAR_PART]->(tp:TabularPart) "
        "WHERE mo.project_name = $p AND tp.name CONTAINS $tab "
        "RETURN mo.name AS object_name, mo.category_name AS category, tp.name AS tabular_name "
        "ORDER BY mo.name",
        {"p": PROJECT_NAME, "tab": tabular},
    )
    return {"objects": rows}


def _find_objects_by_attribute_in_tabular(req):
    attr_name = req.get("attribute", "")
    rows = run_query(
        "MATCH (mo:MetadataObject)-[:HAS_TABULAR_PART]->(tp:TabularPart)-[:HAS_ATTRIBUTE]->(a:Attribute) "
        "WHERE mo.project_name = $p AND a.name CONTAINS $attr "
        "RETURN mo.name AS object_name, tp.name AS tabular_name, a.name AS attribute_name "
        "ORDER BY mo.name",
        {"p": PROJECT_NAME, "attr": attr_name},
    )
    return {"objects": rows}


def _list_forms(req):
    name = _get_name(req)
    qn = _find_qn(req)
    if not qn:
        return {"forms": []}
    rows = run_query(
        "MATCH (mo:MetadataObject)-[:HAS_FORM]->(f:Form) "
        "WHERE mo.qualified_name = $qn "
        "RETURN f.name AS name, f.qualified_name AS qn ORDER BY f.name",
        {"qn": qn},
    )
    return {"forms": rows}


def _list_form_controls(req):
    form_qn = req.get("form_qn", "")
    name = _get_name(req)
    form_name = req.get("form", "")
    if form_qn:
        rows = run_query(
            "MATCH (fc:FormControl) WHERE fc.qualified_name STARTS WITH $qn "
            "RETURN fc.name AS name, fc.control_type AS type, fc.data_path AS data_path "
            "ORDER BY fc.name",
            {"qn": form_qn},
        )
    else:
        rows = run_query(
            "MATCH (fc:FormControl) "
            "WHERE fc.qualified_name CONTAINS $name AND fc.qualified_name CONTAINS $form "
            "RETURN fc.name AS name, fc.control_type AS type, fc.data_path AS data_path "
            "ORDER BY fc.name",
            {"name": name, "form": form_name},
        )
    return {"controls": rows}


def _list_form_events(req):
    name = _get_name(req)
    rows = run_query(
        "MATCH (fe:FormEvent) WHERE fe.qualified_name CONTAINS $name "
        "RETURN fe.name AS event, fe.handler AS handler, fe.qualified_name AS qn "
        "ORDER BY fe.name",
        {"name": name},
    )
    return {"events": rows}


def _list_form_event_handlers(req):
    return _list_form_events(req)


def _list_form_attributes_of_form(req):
    name = _get_name(req)
    rows = run_query(
        "MATCH (fa:FormAttribute) WHERE fa.qualified_name CONTAINS $name "
        "RETURN fa.name AS name, fa.qualified_name AS qn ORDER BY fa.name",
        {"name": name},
    )
    return {"attributes": rows}


def _list_form_commands(req):
    name = _get_name(req)
    rows = run_query(
        "MATCH (fc:FormControl) WHERE fc.qualified_name CONTAINS $name AND fc.control_type = 'UsualButton' "
        "RETURN fc.name AS name, fc.qualified_name AS qn ORDER BY fc.name",
        {"name": name},
    )
    return {"commands": rows}


def _list_form_bindings(req):
    name = _get_name(req)
    rows = run_query(
        "MATCH (fc:FormControl)-[:BINDS_TO]->(fa:FormAttribute) "
        "WHERE fc.qualified_name CONTAINS $name "
        "RETURN fc.name AS control, fa.name AS attribute, fc.qualified_name AS qn "
        "ORDER BY fc.name",
        {"name": name},
    )
    return {"bindings": rows}


def _find_controls_bound_to(req):
    attr = req.get("attribute", req.get("data_path", ""))
    rows = run_query(
        "MATCH (fc:FormControl)-[:BINDS_TO]->(fa:FormAttribute) "
        "WHERE fa.name CONTAINS $attr "
        "RETURN fc.name AS control, fa.name AS attribute, fc.qualified_name AS qn "
        "ORDER BY fc.name",
        {"attr": attr},
    )
    return {"controls": rows}


def _get_default_forms(req):
    name = _get_name(req)
    qn = _find_qn(req)
    if not qn:
        return {"forms": []}
    obj = run_query(
        "MATCH (mo:MetadataObject) WHERE mo.qualified_name = $qn RETURN properties(mo) AS props",
        {"qn": qn},
    )
    if not obj:
        return {"forms": []}
    props = obj[0]["props"]
    form_keys = [k for k in props if "Форма" in k or "Form" in k]
    return {"default_forms": {k: props[k] for k in form_keys if props[k]}}


def _list_commands(req):
    name = _get_name(req)
    qn = _find_qn(req)
    if not qn:
        return {"commands": []}
    rows = run_query(
        "MATCH (mo:MetadataObject)-[:HAS_COMMAND]->(cmd:Command) "
        "WHERE mo.qualified_name = $qn "
        "RETURN cmd.name AS name, cmd.qualified_name AS qn ORDER BY cmd.name",
        {"qn": qn},
    )
    return {"commands": rows}


def _list_layouts(req):
    name = _get_name(req)
    qn = _find_qn(req)
    if not qn:
        return {"layouts": []}
    rows = run_query(
        "MATCH (mo:MetadataObject)-[:HAS_LAYOUT]->(l:Layout) "
        "WHERE mo.qualified_name = $qn "
        "RETURN l.name AS name, l.qualified_name AS qn ORDER BY l.name",
        {"qn": qn},
    )
    return {"layouts": rows}


def _list_modules_of_owner(req):
    name = _get_name(req)
    if name:
        suffix = f"/{name}"
        infix = f"/{name}/"
        rows = run_query(
            "MATCH (m:Module) WHERE m.project_name = $p "
            "AND (m.owner_qn ENDS WITH $suffix OR m.owner_qn CONTAINS $infix) "
            "RETURN m.name AS name, m.module_type AS module_type, m.id AS id, m.owner_qn AS owner "
            "ORDER BY m.name",
            {"p": PROJECT_NAME, "suffix": suffix, "infix": infix},
        )
    else:
        rows = []
    return {"modules": rows}


def _list_module_routines(req):
    module_id = req.get("module_id", "")
    owner = req.get("owner", _get_name(req))
    if module_id:
        rows = run_query(
            "MATCH (m:Module)-[:DECLARES]->(r:Routine) WHERE m.id = $mid "
            "RETURN r.name AS name, r.routine_type AS type, r.export AS export, "
            "r.directive AS directive, r.signature AS signature, r.id AS id "
            "ORDER BY r.line",
            {"mid": module_id},
        )
    else:
        rows = run_query(
            "MATCH (r:Routine) WHERE r.project_name = $p AND r.owner_qn CONTAINS $owner "
            "RETURN r.name AS name, r.routine_type AS type, r.export AS export, "
            "r.directive AS directive, r.signature AS signature, r.id AS id "
            "ORDER BY r.line",
            {"p": PROJECT_NAME, "owner": owner},
        )
    return {"routines": rows}


def _list_common_module_routines(req):
    module_name = req.get("module", _get_name(req))
    rows = run_query(
        "MATCH (r:Routine) WHERE r.project_name = $p AND r.owner_qn CONTAINS $module "
        "RETURN r.name AS name, r.routine_type AS type, r.export AS export, "
        "r.directive AS directive, r.signature AS signature, r.id AS id "
        "ORDER BY r.line",
        {"p": PROJECT_NAME, "module": module_name},
    )
    return {"routines": rows}


def _list_exported_routines(req):
    name = _get_name(req)
    rows = run_query(
        "MATCH (r:Routine) WHERE r.project_name = $p AND r.export = true "
        "AND r.owner_qn CONTAINS $name "
        "RETURN r.name AS name, r.routine_type AS type, "
        "r.directive AS directive, r.signature AS signature, r.id AS id, r.owner_qn AS owner "
        "ORDER BY r.name",
        {"p": PROJECT_NAME, "name": name},
    )
    return {"routines": rows}


def _get_routine_body(req):
    routine_id = req.get("id", req.get("routine_id", ""))
    name = req.get("name", "")
    owner = req.get("owner", "")
    if routine_id:
        rows = run_query(
            "MATCH (r:Routine) WHERE r.id = $id "
            "RETURN r.name AS name, r.body AS body, r.signature AS signature, "
            "r.owner_qn AS owner, r.doc_description AS doc, r.line AS line, r.file_path AS file_path",
            {"id": routine_id},
        )
    elif name and owner:
        rows = run_query(
            "MATCH (r:Routine) WHERE r.project_name = $p AND r.name = $name "
            "AND r.owner_qn CONTAINS $owner "
            "RETURN r.name AS name, r.body AS body, r.signature AS signature, "
            "r.owner_qn AS owner, r.doc_description AS doc, r.line AS line, r.file_path AS file_path "
            "LIMIT 1",
            {"p": PROJECT_NAME, "name": name, "owner": owner},
        )
    elif name:
        rows = run_query(
            "MATCH (r:Routine) WHERE r.project_name = $p AND r.name = $name "
            "RETURN r.name AS name, r.body AS body, r.signature AS signature, "
            "r.owner_qn AS owner, r.doc_description AS doc, r.line AS line, r.file_path AS file_path "
            "LIMIT 1",
            {"p": PROJECT_NAME, "name": name},
        )
    else:
        return {"error": "Provide 'id' or 'name'"}
    if rows:
        return rows[0]
    return {"error": f"Routine not found: {name or routine_id}"}


def _find_routines_by_name(req):
    name = _get_name(req)
    rows = run_query(
        "MATCH (r:Routine) WHERE r.project_name = $p AND r.name CONTAINS $name "
        "RETURN r.name AS name, r.routine_type AS type, r.export AS export, "
        "r.directive AS directive, r.signature AS signature, "
        "r.id AS id, r.owner_qn AS owner "
        "ORDER BY r.name LIMIT 50",
        {"p": PROJECT_NAME, "name": name},
    )
    return {"routines": rows}


def _find_routines_by_signature(req):
    sig = req.get("signature", req.get("text", _get_name(req)))
    rows = run_query(
        "MATCH (r:Routine) WHERE r.project_name = $p AND r.signature CONTAINS $sig "
        "RETURN r.name AS name, r.signature AS signature, r.id AS id, r.owner_qn AS owner "
        "ORDER BY r.name LIMIT 50",
        {"p": PROJECT_NAME, "sig": sig},
    )
    return {"routines": rows}


def _find_unused_routines(req):
    rows = run_query(
        "MATCH (r:Routine) WHERE r.project_name = $p AND r.export = false "
        "AND NOT EXISTS { MATCH ()-[:CALLS]->(r) } "
        "RETURN r.name AS name, r.owner_qn AS owner, r.id AS id "
        "ORDER BY r.name LIMIT 100",
        {"p": PROJECT_NAME},
    )
    return {"routines": rows}


def _list_callers_of_routine(req):
    name = req.get("name", "")
    routine_id = req.get("id", req.get("routine_id", ""))
    if routine_id:
        rows = run_query(
            "MATCH (caller:Routine)-[:CALLS]->(r:Routine {id: $id}) "
            "RETURN caller.name AS name, caller.owner_qn AS owner, caller.id AS id",
            {"id": routine_id},
        )
    elif name:
        rows = run_query(
            "MATCH (caller:Routine)-[:CALLS]->(r:Routine {name: $name}) "
            "RETURN caller.name AS name, caller.owner_qn AS owner, caller.id AS id",
            {"name": name},
        )
    else:
        return {"callers": [], "error": "Provide 'id' or 'name'"}
    return {"callers": rows}


def _list_callees_of_routine(req):
    name = req.get("name", "")
    routine_id = req.get("id", "")
    if routine_id:
        rows = run_query(
            "MATCH (r:Routine)-[:CALLS]->(callee:Routine) WHERE r.id = $id "
            "RETURN callee.name AS name, callee.owner_qn AS owner, callee.id AS id",
            {"id": routine_id},
        )
    elif name:
        rows = run_query(
            "MATCH (r:Routine)-[:CALLS]->(callee:Routine) "
            "WHERE r.project_name = $p AND r.name = $name "
            "RETURN callee.name AS name, callee.owner_qn AS owner, callee.id AS id",
            {"p": PROJECT_NAME, "name": name},
        )
    else:
        return {"callees": [], "error": "Provide 'id' or 'name'"}
    return {"callees": rows}


def _call_graph_subtree(req):
    name = req.get("name", "")
    routine_id = req.get("id", "")
    depth = req.get("depth", 3)
    if routine_id:
        condition = "r.id = $id"
        params = {"id": routine_id, "depth": depth}
    elif name:
        condition = f"r.project_name = '{PROJECT_NAME}' AND r.name = $name"
        params = {"name": name, "depth": depth}
    else:
        return {"error": "Provide 'id' or 'name'"}
    # Memgraph doesn't support variable-length in the same way; use BFS up to depth
    rows = run_query(
        f"MATCH path = (r:Routine)-[:CALLS*1..3]->(callee:Routine) "
        f"WHERE {condition} "
        f"RETURN callee.name AS name, callee.owner_qn AS owner, callee.id AS id",
        params,
    )
    return {"call_tree": rows}


def _find_calls_between_owners(req):
    owner1 = req.get("from", req.get("owner1", ""))
    owner2 = req.get("to", req.get("owner2", ""))
    rows = run_query(
        "MATCH (r1:Routine)-[:CALLS]->(r2:Routine) "
        "WHERE r1.owner_qn CONTAINS $o1 AND r2.owner_qn CONTAINS $o2 "
        "RETURN r1.name AS caller, r1.owner_qn AS caller_owner, "
        "r2.name AS callee, r2.owner_qn AS callee_owner",
        {"o1": owner1, "o2": owner2},
    )
    return {"calls": rows}


def _list_enum_values(req):
    name = _get_name(req)
    qn = _find_qn(req)
    if not qn:
        return {"values": []}
    rows = run_query(
        "MATCH (mo:MetadataObject)-[:HAS_ENUM_VALUE]->(ev:EnumValue) "
        "WHERE mo.qualified_name = $qn "
        "RETURN ev.name AS name, ev.Synonym AS synonym ORDER BY ev.name",
        {"qn": qn},
    )
    return {"values": rows}


def _list_predefined_of_object(req):
    name = _get_name(req)
    qn = _find_qn(req)
    if not qn:
        return {"predefined": []}
    rows = run_query(
        "MATCH (mo:MetadataObject)-[:HAS_PREDEFINED]->(pi:PredefinedItem) "
        "WHERE mo.qualified_name = $qn "
        "RETURN pi.name AS name, pi.description AS description, pi.is_folder AS is_folder "
        "ORDER BY pi.name",
        {"qn": qn},
    )
    return {"predefined": rows}


def _find_predefined_by_name_in_object(req):
    obj_name = _get_name(req)
    pred_name = req.get("predefined", req.get("predefined_name", ""))
    qn = _find_object_qn(obj_name)
    if not qn:
        return {"predefined": []}
    rows = run_query(
        "MATCH (mo:MetadataObject)-[:HAS_PREDEFINED]->(pi:PredefinedItem) "
        "WHERE mo.qualified_name = $qn AND pi.name CONTAINS $pred "
        "RETURN pi.name AS name, pi.description AS description ORDER BY pi.name",
        {"qn": qn, "pred": pred_name},
    )
    return {"predefined": rows}


def _find_predefined_by_flag(req):
    flag = req.get("flag", req.get("is_folder", ""))
    is_folder = str(flag).lower() in ("true", "1", "yes")
    rows = run_query(
        "MATCH (mo:MetadataObject)-[:HAS_PREDEFINED]->(pi:PredefinedItem) "
        "WHERE mo.project_name = $p AND pi.is_folder = $flag "
        "RETURN mo.name AS object, pi.name AS name, pi.description AS description",
        {"p": PROJECT_NAME, "flag": is_folder},
    )
    return {"predefined": rows}


def _find_predefined_by_kind(req):
    return _list_predefined_of_object(req)


def _find_predefined_by_account_type(req):
    return _list_predefined_of_object(req)


def _list_roles_with_access_to_target(req):
    target = req.get("target", _get_name(req))
    # Try by exact name first, then by CONTAINS for short names
    rows = run_query(
        "MATCH (role_node:MetadataObject)-[rel:GRANTS_ACCESS_TO]->(target_node:MetadataObject) "
        "WHERE target_node.name = $target "
        "RETURN role_node.name AS role, rel.rights AS rights ORDER BY role_node.name",
        {"target": target},
    )
    if not rows:
        rows = run_query(
            "MATCH (role_node:MetadataObject)-[rel:GRANTS_ACCESS_TO]->(target_node:MetadataObject) "
            "WHERE target_node.name CONTAINS $target "
            "RETURN role_node.name AS role, target_node.name AS target, rel.rights AS rights "
            "ORDER BY role_node.name",
            {"target": target},
        )
    return {"roles": rows}


def _list_access_targets_of_role(req):
    role = req.get("role", _get_name(req))
    rows = run_query(
        "MATCH (role_node:MetadataObject)-[rel:GRANTS_ACCESS_TO]->(target_node:MetadataObject) "
        "WHERE role_node.project_name = $p AND role_node.name = $role "
        "RETURN target_node.name AS target, target_node.category_name AS category, "
        "rel.rights AS rights "
        "ORDER BY target_node.name",
        {"p": PROJECT_NAME, "role": role},
    )
    return {"targets": rows}


def _get_access_of_role_to_target(req):
    role = req.get("role", "")
    target = req.get("target", "")
    rows = run_query(
        "MATCH (role_node:MetadataObject)-[rel:GRANTS_ACCESS_TO]->(target_node:MetadataObject) "
        "WHERE role_node.project_name = $p AND role_node.name = $role AND target_node.name = $target "
        "RETURN role_node.name AS role, target_node.name AS target, rel.rights AS rights",
        {"p": PROJECT_NAME, "role": role, "target": target},
    )
    if rows:
        return rows[0]
    return {"error": "No access found"}


def _list_event_subscriptions(req):
    rows = run_query(
        "MATCH (es:EventSubscription) WHERE es.qualified_name STARTS WITH $prefix "
        "RETURN es.name AS name, es.qualified_name AS qn ORDER BY es.name",
        {"prefix": f"{PROJECT_NAME}/"},
    )
    return {"subscriptions": rows}


def _list_event_subscriptions_of_object(req):
    name = _get_name(req)
    rows = run_query(
        "MATCH (es:EventSubscription) WHERE es.qualified_name CONTAINS $name "
        "RETURN es.name AS name, es.qualified_name AS qn ORDER BY es.name",
        {"name": name},
    )
    return {"subscriptions": rows}


def _list_http_services(req):
    rows = run_query(
        "MATCH (mc:MetadataCategory)-[:CONTAINS_OBJECT]->(mo:MetadataObject) "
        "WHERE mc.project_name = $p AND mc.name = 'HTTPСервисы' "
        "RETURN mo.name AS name, mo.qualified_name AS qn ORDER BY mo.name",
        {"p": PROJECT_NAME},
    )
    return {"http_services": rows}


def _list_url_templates_of_service(req):
    name = _get_name(req)
    qn = _find_qn(req)
    if not qn:
        return {"templates": []}
    rows = run_query(
        "MATCH (mo:MetadataObject)-[:HAS_URL_TEMPLATE]->(ut:UrlTemplate) "
        "WHERE mo.qualified_name = $qn "
        "RETURN ut.name AS name, ut.qualified_name AS qn ORDER BY ut.name",
        {"qn": qn},
    )
    return {"templates": rows}


def _list_url_methods_of_template(req):
    template_qn = req.get("template_qn", req.get("template", ""))
    rows = run_query(
        "MATCH (ut:UrlTemplate)-[:HAS_URL_METHOD]->(um:UrlMethod) "
        "WHERE ut.qualified_name CONTAINS $tpl "
        "RETURN um.name AS name, um.qualified_name AS qn ORDER BY um.name",
        {"tpl": template_qn},
    )
    return {"methods": rows}


def _find_usages_of_object(req):
    """Find objects that reference this object via type (USED_IN edges)."""
    name = _get_name(req)
    qn = _find_qn(req)
    if not qn:
        return {"usages": []}
    rows = run_query(
        "MATCH (other:MetadataObject)-[:USED_IN]->(mo:MetadataObject) "
        "WHERE mo.qualified_name = $qn "
        "RETURN other.name AS name, other.category_name AS category, "
        "other.qualified_name AS qn "
        "ORDER BY other.name",
        {"qn": qn},
    )
    return {"usages": rows}


def _find_objects_using_object(req):
    """Find objects that this object references via type (outgoing USED_IN edges)."""
    name = _get_name(req)
    qn = _find_qn(req)
    if not qn:
        return {"uses": []}
    rows = run_query(
        "MATCH (mo:MetadataObject)-[:USED_IN]->(target:MetadataObject) "
        "WHERE mo.qualified_name = $qn "
        "RETURN target.name AS name, target.category_name AS category, "
        "target.qualified_name AS qn "
        "ORDER BY target.name",
        {"qn": qn},
    )
    return {"uses": rows}


def _find_documents_making_movements_into_register(req):
    register = req.get("register", _get_name(req))
    rows = run_query(
        "MATCH (doc:MetadataObject)-[:DO_MOVEMENTS_IN]->(reg:MetadataObject) "
        "WHERE reg.name CONTAINS $reg AND reg.project_name = $p "
        "RETURN doc.name AS document, doc.category_name AS category, reg.name AS register",
        {"p": PROJECT_NAME, "reg": register},
    )
    return {"documents": rows}


def _find_journals_by_graph(req):
    name = _get_name(req)
    rows = run_query(
        "MATCH (jg:JournalGraph) WHERE jg.qualified_name CONTAINS $name "
        "RETURN jg.name AS name, jg.qualified_name AS qn",
        {"name": name},
    )
    return {"journals": rows}


def _resolve_qn(req):
    qn = req.get("qn", req.get("qualified_name", _get_name(req)))
    rows = run_query(
        "MATCH (n) WHERE n.qualified_name = $qn "
        "RETURN labels(n)[0] AS label, n.name AS name, properties(n) AS props",
        {"qn": qn},
    )
    if rows:
        return rows[0]
    return {"error": f"Not found: {qn}"}


def _resolve_qn_prefix(req):
    prefix = req.get("prefix", req.get("qn", _get_name(req)))
    rows = run_query(
        "MATCH (n) WHERE n.qualified_name STARTS WITH $prefix "
        "RETURN labels(n)[0] AS label, n.name AS name, n.qualified_name AS qn "
        "ORDER BY n.qualified_name LIMIT 50",
        {"prefix": prefix},
    )
    return {"nodes": rows}


def _find_by_guid(req):
    guid = req.get("guid", req.get("id", ""))
    rows = run_query(
        "MATCH (n) WHERE n.guid = $guid "
        "RETURN labels(n)[0] AS label, n.name AS name, n.qualified_name AS qn",
        {"guid": guid},
    )
    if rows:
        return rows[0]
    return {"error": f"GUID not found: {guid}"}


def _get_node_properties(req):
    qn = req.get("qn", req.get("qualified_name", ""))
    name = _get_name(req)
    if qn:
        rows = run_query(
            "MATCH (n) WHERE n.qualified_name = $qn "
            "RETURN labels(n) AS labels, properties(n) AS props",
            {"qn": qn},
        )
    elif name:
        rows = run_query(
            "MATCH (n) WHERE n.name = $name AND n.project_name = $p "
            "RETURN labels(n) AS labels, properties(n) AS props LIMIT 1",
            {"name": name, "p": PROJECT_NAME},
        )
    else:
        return {"error": "Provide 'qn' or 'name'"}
    if rows:
        return rows[0]
    return {"error": "Node not found"}


# --- Handler registry ---

HANDLERS = {
    "list_categories": _list_categories,
    "list_objects_by_category": _list_objects_by_category,
    "list_objects_by_name": _list_objects_by_name,
    "object_structure": _object_structure,
    "list_attributes": _list_attributes,
    "list_attributes_with_type": _list_attributes_with_type,
    "list_resources": _list_resources,
    "list_resources_with_type": _list_resources_with_type,
    "list_dimensions": _list_dimensions,
    "list_dimensions_with_type": _list_dimensions_with_type,
    "list_characteristics": _list_characteristics,
    "list_characteristics_with_type": _list_characteristics_with_type,
    "list_tabular_parts": _list_tabular_parts,
    "list_tabular_attributes": _list_tabular_attributes,
    "find_objects_with_tabular": _find_objects_with_tabular,
    "find_objects_by_attribute_in_tabular": _find_objects_by_attribute_in_tabular,
    "list_forms": _list_forms,
    "list_form_controls": _list_form_controls,
    "list_form_events": _list_form_events,
    "list_form_event_handlers": _list_form_event_handlers,
    "list_form_attributes_of_form": _list_form_attributes_of_form,
    "list_form_commands": _list_form_commands,
    "list_form_bindings": _list_form_bindings,
    "find_controls_bound_to": _find_controls_bound_to,
    "get_default_forms": _get_default_forms,
    "list_commands": _list_commands,
    "list_layouts": _list_layouts,
    "list_modules_of_owner": _list_modules_of_owner,
    "list_module_routines": _list_module_routines,
    "list_common_module_routines": _list_common_module_routines,
    "list_exported_routines": _list_exported_routines,
    "get_routine_body": _get_routine_body,
    "find_routines_by_name": _find_routines_by_name,
    "find_routines_by_signature": _find_routines_by_signature,
    "find_unused_routines": _find_unused_routines,
    "list_callers_of_routine": _list_callers_of_routine,
    "list_callees_of_routine": _list_callees_of_routine,
    "call_graph_subtree": _call_graph_subtree,
    "find_calls_between_owners": _find_calls_between_owners,
    "list_enum_values": _list_enum_values,
    "list_predefined_of_object": _list_predefined_of_object,
    "find_predefined_by_name_in_object": _find_predefined_by_name_in_object,
    "find_predefined_by_flag": _find_predefined_by_flag,
    "find_predefined_by_kind": _find_predefined_by_kind,
    "find_predefined_by_account_type": _find_predefined_by_account_type,
    "list_roles_with_access_to_target": _list_roles_with_access_to_target,
    "list_access_targets_of_role": _list_access_targets_of_role,
    "get_access_of_role_to_target": _get_access_of_role_to_target,
    "list_event_subscriptions": _list_event_subscriptions,
    "list_event_subscriptions_of_object": _list_event_subscriptions_of_object,
    "list_http_services": _list_http_services,
    "list_url_templates_of_service": _list_url_templates_of_service,
    "list_url_methods_of_template": _list_url_methods_of_template,
    "find_usages_of_object": _find_usages_of_object,
    "find_objects_using_object": _find_objects_using_object,
    "find_documents_making_movements_into_register": _find_documents_making_movements_into_register,
    "find_journals_by_graph": _find_journals_by_graph,
    "resolve_qn": _resolve_qn,
    "resolve_qn_prefix": _resolve_qn_prefix,
    "find_by_guid": _find_by_guid,
    "get_node_properties": _get_node_properties,
}
