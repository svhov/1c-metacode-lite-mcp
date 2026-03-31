"""Loads parsed data into Memgraph."""

import logging
import os

from app.config import (
    PROJECT_NAME, CODE_DIR, METADATA_DIR,
    FULL_METADATA_RELOAD, LOAD_BSL_SIGNATURES,
    LOAD_FORMS_FROM_XML, LOAD_PREDEFINED_VALUES, LOAD_ROLE_RIGHTS,
)
from app.db.connection import run_query, run_write
from app.db.indexes import ensure_indexes
from app.parsers.metadata_report import parse_metadata_report
from app.parsers.bsl_parser import scan_bsl_files
from app.parsers.form_parser import scan_forms
from app.parsers.predefined_parser import scan_predefined
from app.parsers.roles_parser import scan_roles
from app.parsers.config_dump import parse_config_dump

log = logging.getLogger(__name__)

CHUNK_SIZE = 500


def _chunk(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def _project_data_exists() -> bool:
    """Check if project data already loaded."""
    result = run_query(
        "MATCH (p:Project {name: $name}) RETURN p.name AS name",
        {"name": PROJECT_NAME},
    )
    if not result:
        return False
    obj_count = run_query(
        "MATCH (n:MetadataObject {project_name: $name}) RETURN count(n) AS cnt",
        {"name": PROJECT_NAME},
    )
    return obj_count and obj_count[0].get("cnt", 0) > 0


def _clear_project():
    """Remove all data for this project."""
    log.info("Clearing project data: %s", PROJECT_NAME)
    run_write(
        "MATCH (n) WHERE n.project_name = $name DETACH DELETE n",
        {"name": PROJECT_NAME},
    )
    run_write(
        "MATCH (p:Project {name: $name}) DETACH DELETE p",
        {"name": PROJECT_NAME},
    )
    log.info("Project data cleared")


def _load_metadata(config):
    """Load parsed metadata into graph."""
    log.info("Loading metadata: %s (%d objects)", config.config_name, config.object_count)

    # Create Project node
    run_write(
        "MERGE (p:Project {name: $name})",
        {"name": PROJECT_NAME},
    )

    # Create Configuration node
    run_write("""
        MERGE (c:Configuration {qualified_name: $qn})
        SET c.name = $name, c.project_name = $project
        WITH c
        MATCH (p:Project {name: $project})
        MERGE (p)-[:HAS_CONFIGURATION]->(c)
    """, {
        "qn": f"{PROJECT_NAME}/{config.config_name}",
        "name": config.config_name,
        "project": PROJECT_NAME,
    })

    # Set config properties
    for key, val in config.config_props.items():
        safe_key = key.replace(" ", "_").replace(".", "_")
        try:
            run_write(f"""
                MATCH (c:Configuration {{qualified_name: $qn}})
                SET c.`{safe_key}` = $val
            """, {"qn": f"{PROJECT_NAME}/{config.config_name}", "val": val})
        except Exception:
            pass

    # Create categories
    for cat in config.categories:
        cat_qn = f"{PROJECT_NAME}/{config.config_name}/{cat['name']}"
        run_write("""
            MERGE (mc:MetadataCategory {qualified_name: $qn})
            SET mc.name = $name, mc.project_name = $project, mc.config_name = $config
            WITH mc
            MATCH (c:Configuration {qualified_name: $conf_qn})
            MERGE (c)-[:HAS_CATEGORY]->(mc)
        """, {
            "qn": cat_qn,
            "name": cat["name"],
            "project": PROJECT_NAME,
            "config": config.config_name,
            "conf_qn": f"{PROJECT_NAME}/{config.config_name}",
        })

    # Create objects in chunks
    for chunk in _chunk(config.objects, CHUNK_SIZE):
        rows = []
        for obj in chunk:
            qn = f"{PROJECT_NAME}/{config.config_name}/{obj['category']}/{obj['name']}"
            cat_qn = f"{PROJECT_NAME}/{config.config_name}/{obj['category']}"
            row = {
                "qn": qn,
                "name": obj["name"],
                "category": obj["category"],
                "category_en": obj["category_en"],
                "project": PROJECT_NAME,
                "config": config.config_name,
                "synonym": obj["properties"].get("Синоним", ""),
                "comment": obj["properties"].get("Комментарий", ""),
                "cat_qn": cat_qn,
            }
            rows.append(row)

        run_write("""
            UNWIND $rows AS r
            MERGE (mo:MetadataObject {qualified_name: r.qn})
            SET mo.name = r.name,
                mo.category_name = r.category,
                mo.category_name_en = r.category_en,
                mo.project_name = r.project,
                mo.config_name = r.config,
                mo.Synonym = r.synonym,
                mo.Comment = r.comment
            WITH mo, r
            MATCH (mc:MetadataCategory {qualified_name: r.cat_qn})
            MERGE (mc)-[:CONTAINS_OBJECT]->(mo)
        """, {"rows": rows})

    log.info("Loaded %d objects", config.object_count)

    # Load child nodes (attributes, resources, dimensions, etc.)
    _load_children(config)


def _load_children(config):
    """Load child nodes (attributes, tabular parts, etc.)."""
    for obj in config.objects:
        obj_qn = f"{PROJECT_NAME}/{config.config_name}/{obj['category']}/{obj['name']}"
        children = obj.get("children", {})

        for child_type, child_list in children.items():
            label = child_type  # Attribute, Resource, Dimension, etc.
            rel = _child_rel(child_type)

            for child in child_list:
                child_qn = f"{obj_qn}/{child_type}/{child['name']}"
                props = {"qn": child_qn, "name": child["name"], "obj_qn": obj_qn}

                # Add type info if present
                type_val = child.get("properties", {}).get("Тип", "")
                synonym = child.get("properties", {}).get("Синоним", "")

                run_write(f"""
                    MERGE (ch:{label} {{qualified_name: $qn}})
                    SET ch.name = $name, ch.type_info = $type_val, ch.Synonym = $synonym
                    WITH ch
                    MATCH (mo:MetadataObject {{qualified_name: $obj_qn}})
                    MERGE (mo)-[:{rel}]->(ch)
                """, {**props, "type_val": type_val, "synonym": synonym})

                # TabularPart children (attributes of tabular section)
                if child_type == "TabularPart":
                    for tab_child in child.get("children", []):
                        tab_attr_qn = f"{child_qn}/Attribute/{tab_child['name']}"
                        tab_type = tab_child.get("properties", {}).get("Тип", "")
                        run_write("""
                            MERGE (ta:Attribute {qualified_name: $qn})
                            SET ta.name = $name, ta.type_info = $type_val
                            WITH ta
                            MATCH (tp:TabularPart {qualified_name: $tp_qn})
                            MERGE (tp)-[:HAS_ATTRIBUTE]->(ta)
                        """, {
                            "qn": tab_attr_qn,
                            "name": tab_child["name"],
                            "type_val": tab_type,
                            "tp_qn": child_qn,
                        })


def _child_rel(child_type: str) -> str:
    return {
        "Attribute": "HAS_ATTRIBUTE",
        "Resource": "HAS_RESOURCE",
        "Dimension": "HAS_DIMENSION",
        "Characteristic": "HAS_CHARACTERISTIC",
        "TabularPart": "HAS_TABULAR_PART",
        "EnumValue": "HAS_ENUM_VALUE",
        "Form": "HAS_FORM",
        "Layout": "HAS_LAYOUT",
        "Command": "HAS_COMMAND",
        "AccountingFlag": "HAS_ACCOUNTING_FLAG",
        "DimensionAccountingFlag": "HAS_DIM_ACCOUNTING_FLAG",
        "JournalGraph": "HAS_JOURNAL_GRAPH",
        "UrlTemplate": "HAS_URL_TEMPLATE",
        "UrlMethod": "HAS_URL_METHOD",
    }.get(child_type, "HAS_CHILD")


def _load_routines(routines: list[dict]):
    """Load BSL routines and modules into graph."""
    log.info("Loading %d routines...", len(routines))

    # Group by owner to create Module nodes
    owners = {}
    for r in routines:
        owners.setdefault(r["owner_qn"], []).append(r)

    for owner_qn, owner_routines in owners.items():
        # Create Module node
        parts = owner_qn.split("/")
        module_name = parts[-1] if len(parts) > 2 else owner_qn

        # Determine module type from file path
        first_routine = owner_routines[0]
        fp = first_routine.get("file_path", "")
        if "ObjectModule" in fp or "/Ext/ObjectModule.bsl" in fp:
            module_type = "ObjectModule"
        elif "ManagerModule" in fp or "/Ext/ManagerModule.bsl" in fp:
            module_type = "ManagerModule"
        elif "CommonModules" in fp:
            module_type = "CommonModule"
        elif "Form" in fp:
            module_type = "FormModule"
        else:
            module_type = "Module"

        module_id = f"module:{owner_qn}"
        run_write("""
            MERGE (m:Module {id: $id})
            SET m.name = $name, m.module_type = $mtype,
                m.owner_qn = $owner_qn,
                m.project_name = $project, m.config_name = $config,
                m.path = $path
        """, {
            "id": module_id,
            "name": module_name,
            "mtype": module_type,
            "owner_qn": owner_qn,
            "project": first_routine["project_name"],
            "config": first_routine["config_name"],
            "path": fp,
        })

        # Link module to owner MetadataObject if possible
        # owner_qn format: project/config/Category/ObjectName[/Form/FormName]
        if len(parts) >= 4:
            obj_qn = "/".join(parts[:4])
            run_write("""
                MATCH (mo:MetadataObject {qualified_name: $obj_qn})
                MATCH (m:Module {id: $mid})
                MERGE (mo)-[:HAS_MODULE]->(m)
            """, {"obj_qn": obj_qn, "mid": module_id})

    # Load routines in chunks
    for chunk in _chunk(routines, CHUNK_SIZE):
        rows = [{
            "id": r["id"],
            "name": r["name"],
            "routine_type": r["routine_type"],
            "directive": r["directive"],
            "export": r["export"],
            "is_ssl_api": r["is_ssl_api"],
            "signature": r["signature"],
            "params_text": r["params_text"],
            "param_names": r["param_names"],
            "body": r["body"],
            "line": r["line"],
            "file_path": r["file_path"],
            "area_path": r["area_path"],
            "owner_qn": r["owner_qn"],
            "project_name": r["project_name"],
            "config_name": r["config_name"],
            "doc_description": r.get("doc_description", ""),
            "doc_params_text": r.get("doc_params_text", ""),
            "doc_return_text": r.get("doc_return_text", ""),
            "module_id": f"module:{r['owner_qn']}",
        } for r in chunk]

        run_write("""
            UNWIND $rows AS r
            MERGE (rt:Routine {id: r.id})
            SET rt.name = r.name,
                rt.routine_type = r.routine_type,
                rt.directive = r.directive,
                rt.export = r.export,
                rt.is_ssl_api = r.is_ssl_api,
                rt.signature = r.signature,
                rt.params_text = r.params_text,
                rt.param_names = r.param_names,
                rt.body = r.body,
                rt.line = r.line,
                rt.file_path = r.file_path,
                rt.area_path = r.area_path,
                rt.owner_qn = r.owner_qn,
                rt.project_name = r.project_name,
                rt.config_name = r.config_name,
                rt.doc_description = r.doc_description,
                rt.doc_params_text = r.doc_params_text,
                rt.doc_return_text = r.doc_return_text
            WITH rt, r
            MATCH (m:Module {id: r.module_id})
            MERGE (m)-[:DECLARES]->(rt)
        """, {"rows": rows})

    # Build CALLS relationships
    _build_calls(routines)
    log.info("Routines loaded")


def _build_calls(routines: list[dict]):
    """Build CALLS edges between routines."""
    # Build name -> id index for this project
    name_to_id = {}
    for r in routines:
        name_to_id.setdefault(r["name"], []).append(r["id"])

    call_pairs = []
    for r in routines:
        for call_name in r.get("calls", []):
            targets = name_to_id.get(call_name, [])
            for target_id in targets:
                if target_id != r["id"]:
                    call_pairs.append({"from_id": r["id"], "to_id": target_id})

    if call_pairs:
        for chunk in _chunk(call_pairs, CHUNK_SIZE):
            run_write("""
                UNWIND $pairs AS p
                MATCH (a:Routine {id: p.from_id})
                MATCH (b:Routine {id: p.to_id})
                MERGE (a)-[:CALLS]->(b)
            """, {"pairs": chunk})
        log.info("Created %d CALLS edges", len(call_pairs))


def _load_forms(forms: list[dict]):
    """Load form controls, attributes, events into graph."""
    log.info("Loading %d forms...", len(forms))
    for form_data in forms:
        owner_qn = form_data["owner_qn"]

        for ctrl in form_data.get("controls", []):
            run_write("""
                MERGE (fc:FormControl {qualified_name: $qn})
                SET fc.name = $name, fc.control_type = $ctype, fc.data_path = $dp
            """, {
                "qn": ctrl["qualified_name"],
                "name": ctrl["name"],
                "ctype": ctrl["type"],
                "dp": ctrl["data_path"],
            })
            # Bind to data path if present
            if ctrl["data_path"]:
                run_write("""
                    MATCH (fc:FormControl {qualified_name: $ctrl_qn})
                    MATCH (fa:FormAttribute {qualified_name: $fa_qn})
                    MERGE (fc)-[:BINDS_TO]->(fa)
                """, {
                    "ctrl_qn": ctrl["qualified_name"],
                    "fa_qn": f"{owner_qn}/Attribute/{ctrl['data_path']}",
                })

        for attr in form_data.get("attributes", []):
            run_write("""
                MERGE (fa:FormAttribute {qualified_name: $qn})
                SET fa.name = $name
            """, {"qn": attr["qualified_name"], "name": attr["name"]})

        for event in form_data.get("events", []):
            run_write("""
                MERGE (fe:FormEvent {qualified_name: $qn})
                SET fe.name = $name, fe.handler = $handler
            """, {
                "qn": event["qualified_name"],
                "name": event["name"],
                "handler": event["handler"],
            })

    log.info("Forms loaded")


def _load_predefined(items: list[dict]):
    """Load predefined items into graph."""
    log.info("Loading %d predefined items...", len(items))
    for chunk in _chunk(items, CHUNK_SIZE):
        rows = [{
            "qn": it["qualified_name"],
            "name": it["name"],
            "description": it["description"],
            "is_folder": it["is_folder"],
        } for it in chunk]

        run_write("""
            UNWIND $rows AS r
            MERGE (pi:PredefinedItem {qualified_name: r.qn})
            SET pi.name = r.name, pi.description = r.description, pi.is_folder = r.is_folder
        """, {"rows": rows})

        # Link to owner by extracting object name from file path
        for it in chunk:
            # owner_qn from file: project/config/Catalogs/ObjectName
            # Extract object name (last part before /Predefined/)
            parts = it["owner_qn"].split("/")
            obj_name = parts[-1] if parts else ""
            if obj_name:
                run_write("""
                    MATCH (mo:MetadataObject)
                    WHERE mo.project_name = $project AND mo.name = $obj_name
                    MATCH (pi:PredefinedItem {qualified_name: $pi_qn})
                    MERGE (mo)-[:HAS_PREDEFINED]->(pi)
                """, {
                    "project": PROJECT_NAME,
                    "obj_name": obj_name,
                    "pi_qn": it["qualified_name"],
                })

    log.info("Predefined items loaded")


def _load_roles(grants: list[dict], config_name: str):
    """Load role access rights into graph."""
    log.info("Loading %d role grants...", len(grants))
    for grant in grants:
        # Ensure Role node exists as MetadataObject
        role_qn = f"{PROJECT_NAME}/{config_name}/Роли/{grant['role_name']}"

        # Target uses dot notation (e.g. Catalog.SomeName) -> find MetadataObject
        rights_str = ",".join(grant["rights"].keys())
        run_write("""
            MATCH (role:MetadataObject {qualified_name: $role_qn})
            MATCH (target:MetadataObject)
            WHERE target.name = $target_name AND target.project_name = $project
            MERGE (role)-[r:GRANTS_ACCESS_TO]->(target)
            SET r.rights = $rights
        """, {
            "role_qn": role_qn,
            "target_name": grant["target_name"].split(".")[-1],
            "project": PROJECT_NAME,
            "rights": rights_str,
        })

    log.info("Role grants loaded")


def _load_guid_map(guid_map: dict[str, str]):
    """Store GUID mapping on existing nodes."""
    log.info("Mapping %d GUIDs...", len(guid_map))
    for name, uid in guid_map.items():
        # name format: Catalog.ObjectName or Catalog.ObjectName.Attribute.AttrName
        parts = name.split(".")
        obj_name = parts[1] if len(parts) >= 2 else parts[0]
        run_write("""
            MATCH (n {name: $name})
            WHERE n.project_name = $project
            SET n.guid = $uid
        """, {"name": obj_name, "project": PROJECT_NAME, "uid": uid})


def _build_used_in():
    """Build USED_IN edges from type references in attributes, dimensions, resources.

    Parses type_info like 'СправочникСсылка.Контрагенты' to create
    USED_IN edges from the owning MetadataObject to the referenced one.
    """
    import re

    # Type prefixes that indicate references to other metadata objects
    REF_PATTERN = re.compile(
        r"(СправочникСсылка|ДокументСсылка|ПеречислениеСсылка|"
        r"ПланВидовХарактеристикСсылка|ПланСчетовСсылка|ПланВидовРасчетаСсылка|"
        r"БизнесПроцессСсылка|ЗадачаСсылка|"
        r"РегистрСведенийНаборЗаписей|РегистрНакопленияНаборЗаписей|"
        r"CatalogRef|DocumentRef|EnumRef)\."
        r"(\w+)"
    )

    # Get all nodes with type_info that contain references
    rows = run_query("""
        MATCH (owner:MetadataObject)-[]->(child)
        WHERE child.type_info IS NOT NULL AND child.type_info CONTAINS 'Ссылка'
        RETURN DISTINCT owner.qualified_name AS owner_qn, child.type_info AS type_info
    """)

    pairs = set()
    for row in rows:
        owner_qn = row["owner_qn"]
        type_info = row["type_info"]
        # type_info can have multiple types comma-separated
        for match in REF_PATTERN.finditer(type_info):
            ref_name = match.group(2)
            pairs.add((owner_qn, ref_name))

    if not pairs:
        log.info("No USED_IN references found in type_info")
        return

    log.info("Building %d USED_IN edges from type references...", len(pairs))
    pair_list = [{"owner_qn": p[0], "ref_name": p[1]} for p in pairs]

    for chunk in _chunk(pair_list, CHUNK_SIZE):
        run_write("""
            UNWIND $pairs AS p
            MATCH (owner:MetadataObject {qualified_name: p.owner_qn})
            MATCH (target:MetadataObject {name: p.ref_name})
            MERGE (owner)-[:USED_IN]->(target)
        """, {"pairs": chunk})

    log.info("USED_IN edges created")


def load_all():
    """Main entry point — load all data for this project."""
    log.info("=" * 60)
    log.info("Starting data load for project: %s", PROJECT_NAME)
    log.info("=" * 60)

    ensure_indexes()

    # Check if data already exists
    if not FULL_METADATA_RELOAD and _project_data_exists():
        result = run_query(
            "MATCH (n:MetadataObject {project_name: $name}) RETURN count(n) AS cnt",
            {"name": PROJECT_NAME},
        )
        cnt = result[0]["cnt"] if result else 0
        log.info("[OK] Project metadata already loaded (%d objects)", cnt)
        return

    if FULL_METADATA_RELOAD:
        _clear_project()

    # 1. Parse metadata report
    metadata_files = [
        f for f in os.listdir(METADATA_DIR)
        if f.endswith(".txt")
    ] if os.path.isdir(METADATA_DIR) else []

    if not metadata_files:
        log.warning("No metadata files found in %s", METADATA_DIR)
        return

    config = parse_metadata_report(os.path.join(METADATA_DIR, metadata_files[0]))
    _load_metadata(config)

    # 2. Parse ConfigDumpInfo.xml for GUIDs
    dump_file = os.path.join(CODE_DIR, "ConfigDumpInfo.xml")
    if os.path.exists(dump_file):
        guid_map = parse_config_dump(dump_file)
        _load_guid_map(guid_map)

    # 3. BSL routines
    if LOAD_BSL_SIGNATURES and os.path.isdir(CODE_DIR):
        routines = scan_bsl_files(CODE_DIR, PROJECT_NAME, config.config_name)
        if routines:
            _load_routines(routines)

    # 4. Forms
    if LOAD_FORMS_FROM_XML and os.path.isdir(CODE_DIR):
        forms = scan_forms(CODE_DIR, PROJECT_NAME, config.config_name)
        if forms:
            _load_forms(forms)

    # 5. Predefined
    if LOAD_PREDEFINED_VALUES and os.path.isdir(CODE_DIR):
        predefined = scan_predefined(CODE_DIR, PROJECT_NAME, config.config_name)
        if predefined:
            _load_predefined(predefined)

    # 6. Roles
    if LOAD_ROLE_RIGHTS and os.path.isdir(CODE_DIR):
        grants = scan_roles(CODE_DIR, PROJECT_NAME, config.config_name)
        if grants:
            _load_roles(grants, config.config_name)

    # 7. Build USED_IN relationships from type references
    _build_used_in()

    # Final stats
    stats = run_query("""
        MATCH (n) WHERE n.project_name = $name
        RETURN labels(n)[0] AS label, count(n) AS cnt
        ORDER BY cnt DESC
    """, {"name": PROJECT_NAME})
    log.info("Load complete. Stats:")
    for s in stats:
        log.info("  %s: %d", s["label"], s["cnt"])
