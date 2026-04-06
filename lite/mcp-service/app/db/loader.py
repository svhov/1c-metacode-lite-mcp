"""Loads parsed data into Memgraph."""

import logging
import os

from app.config import (
    PROJECT_NAME, CODE_DIR, METADATA_DIR,
    FULL_METADATA_RELOAD, LOAD_BSL_SIGNATURES,
    LOAD_FORMS_FROM_XML, LOAD_PREDEFINED_VALUES, LOAD_ROLE_RIGHTS,
    ENABLE_EMBEDDING, EMBEDDING_MODEL_PATH, EMBEDDING_DB_PATH,
    EMBEDDING_BATCH_SIZE, EMBEDDING_QUERY_PREFIX, EMBEDDING_PASSAGE_PREFIX,
    RERANKER_MODEL_PATH,
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


import re as _re

_VALID_CYPHER_LABEL = _re.compile(r"^[A-Za-z_]\w*$")


def _load_children(config):
    """Load child nodes (attributes, tabular parts, etc.)."""
    for obj in config.objects:
        obj_qn = f"{PROJECT_NAME}/{config.config_name}/{obj['category']}/{obj['name']}"
        children = obj.get("children", {})

        for child_type, child_list in children.items():
            if not _VALID_CYPHER_LABEL.match(child_type):
                log.warning("Skipping invalid child_type: %r", child_type)
                continue
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
            "param_names": ", ".join(r["param_names"]) if isinstance(r["param_names"], list) else r["param_names"],
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
        r"("
        # Catalog/Document/Enum references
        r"СправочникСсылка|ДокументСсылка|ПеречислениеСсылка|"
        # Plans
        r"ПланВидовХарактеристикСсылка|ПланСчетовСсылка|ПланВидовРасчетаСсылка|"
        # Business processes
        r"БизнесПроцессСсылка|ЗадачаСсылка|"
        # Register record sets
        r"РегистрСведенийНаборЗаписей|РегистрНакопленияНаборЗаписей|"
        r"РегистрБухгалтерииНаборЗаписей|РегистрРасчетаНаборЗаписей|"
        # Register record types
        r"РегистрСведенийЗапись|РегистрНакопленияЗапись|"
        # Register keys and managers
        r"РегистрСведенийКлючЗаписи|"
        r"РегистрСведенийМенеджер|РегистрНакопленияМенеджер|"
        # English equivalents
        r"CatalogRef|DocumentRef|EnumRef|"
        r"ChartOfCharacteristicTypesRef|ChartOfAccountsRef|ChartOfCalculationTypesRef|"
        r"InformationRegisterRecordSet|AccumulationRegisterRecordSet|"
        r"AccountingRegisterRecordSet|CalculationRegisterRecordSet"
        r")\."
        r"(\w+)"
    )

    # Get all nodes with type_info that contain references
    rows = run_query("""
        MATCH (owner:MetadataObject)-[]->(child)
        WHERE child.type_info IS NOT NULL
          AND (child.type_info CONTAINS 'Ссылка'
               OR child.type_info CONTAINS 'НаборЗаписей'
               OR child.type_info CONTAINS 'Запись'
               OR child.type_info CONTAINS 'Менеджер'
               OR child.type_info CONTAINS 'Ref'
               OR child.type_info CONTAINS 'RecordSet')
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


# Map BSL plural category names -> metadata category names
_BSL_CATEGORY_TO_META = {
    "Справочники": "Справочники", "Документы": "Документы",
    "Перечисления": "Перечисления",
    "РегистрыСведений": "РегистрыСведений", "РегистрыНакопления": "РегистрыНакопления",
    "РегистрыБухгалтерии": "РегистрыБухгалтерии", "РегистрыРасчета": "РегистрыРасчета",
    "ПланыВидовХарактеристик": "ПланыВидовХарактеристик",
    "ПланыСчетов": "ПланыСчетов", "ПланыВидовРасчета": "ПланыВидовРасчета",
    "БизнесПроцессы": "БизнесПроцессы", "Задачи": "Задачи",
    "Константы": "Константы", "Обработки": "Обработки", "Отчеты": "Отчеты",
    "Catalogs": "Справочники", "Documents": "Документы", "Enums": "Перечисления",
    "InformationRegisters": "РегистрыСведений", "AccumulationRegisters": "РегистрыНакопления",
    "AccountingRegisters": "РегистрыБухгалтерии", "CalculationRegisters": "РегистрыРасчета",
    "ChartsOfCharacteristicTypes": "ПланыВидовХарактеристик",
    "ChartsOfAccounts": "ПланыСчетов", "ChartsOfCalculationTypes": "ПланыВидовРасчета",
    "BusinessProcesses": "БизнесПроцессы", "Tasks": "Задачи",
    "Constants": "Константы", "DataProcessors": "Обработки", "Reports": "Отчеты",
}


def _build_code_used_in(routines: list[dict]):
    """Build USED_IN edges from BSL code metadata references."""
    pairs = set()
    for r in routines:
        owner_parts = r["owner_qn"].split("/")
        if len(owner_parts) < 4:
            continue
        owner_obj_qn = "/".join(owner_parts[:4])

        for category_bsl, obj_name in r.get("metadata_refs", []):
            meta_cat = _BSL_CATEGORY_TO_META.get(category_bsl)
            if meta_cat:
                pairs.add((owner_obj_qn, meta_cat, obj_name))

    if not pairs:
        return

    log.info("Building %d code-based USED_IN edges...", len(pairs))
    pair_list = [{"owner_qn": p[0], "cat": p[1], "ref_name": p[2]} for p in pairs]

    for chunk in _chunk(pair_list, CHUNK_SIZE):
        run_write("""
            UNWIND $pairs AS p
            MATCH (owner:MetadataObject {qualified_name: p.owner_qn})
            MATCH (target:MetadataObject {name: p.ref_name, category_name: p.cat})
            WHERE target.project_name = $project
            MERGE (owner)-[:USED_IN {source: 'code'}]->(target)
        """, {"pairs": chunk, "project": PROJECT_NAME})

    log.info("Code-based USED_IN edges created")


_RE_MOVEMENT = _re.compile(
    r"\bДвижения\.(\w+)\s*\.\s*(Записать|Добавить|Записывать|Очистить|Write|Add|Clear)\b",
    _re.IGNORECASE,
)


def _build_movements(routines: list[dict]):
    """Build DO_MOVEMENTS_IN edges from Document -> Register patterns in BSL code."""
    pairs = set()
    for r in routines:
        owner_parts = r["owner_qn"].split("/")
        if len(owner_parts) < 4:
            continue
        # Only process document modules
        if owner_parts[2] not in ("Documents", "Документы"):
            continue

        owner_obj_qn = "/".join(owner_parts[:4])
        body = r.get("body", "")
        for m in _RE_MOVEMENT.finditer(body):
            register_name = m.group(1)
            pairs.add((owner_obj_qn, register_name))

    if not pairs:
        log.info("No DO_MOVEMENTS_IN references found in BSL code")
        return

    log.info("Building %d DO_MOVEMENTS_IN edges...", len(pairs))
    pair_list = [{"doc_qn": p[0], "reg_name": p[1]} for p in pairs]

    for chunk in _chunk(pair_list, CHUNK_SIZE):
        run_write("""
            UNWIND $pairs AS p
            MATCH (doc:MetadataObject {qualified_name: p.doc_qn})
            MATCH (reg:MetadataObject {name: p.reg_name})
            WHERE reg.project_name = $project
              AND reg.category_name IN [
                  'РегистрыСведений', 'РегистрыНакопления',
                  'РегистрыБухгалтерии', 'РегистрыРасчета']
            MERGE (doc)-[:DO_MOVEMENTS_IN]->(reg)
        """, {"pairs": chunk, "project": PROJECT_NAME})

    log.info("DO_MOVEMENTS_IN edges created")


def _split_camel_case(name: str) -> str:
    """Split CamelCase/underscore name into words for embedding.

    АМЕ_ВидыОпераций → АМЕ Виды Операций
    ДокументыПредприятия → Документы Предприятия
    """
    # Replace underscores with spaces
    s = name.replace("_", " ")
    # Insert space before uppercase letters that follow lowercase
    result = []
    for i, ch in enumerate(s):
        if i > 0 and ch.isupper() and s[i - 1].islower():
            result.append(" ")
        result.append(ch)
    return "".join(result)


def _build_embeddings():
    """Generate embeddings for routines and metadata objects with incremental updates."""
    from app.services.embedding import init, get_embedder, get_store, text_hash

    init(EMBEDDING_MODEL_PATH, EMBEDDING_DB_PATH,
         reranker_path=RERANKER_MODEL_PATH,
         query_prefix=EMBEDDING_QUERY_PREFIX,
         passage_prefix=EMBEDDING_PASSAGE_PREFIX)
    embedder = get_embedder()
    store = get_store()
    if embedder is None or store is None:
        log.warning("Embedding service not available, skipping")
        return

    # --- Embed routines ---
    rows = run_query("""
        MATCH (r:Routine {project_name: $p})
        RETURN r.id AS id, r.name AS name, r.signature AS signature,
               r.doc_description AS doc_description,
               r.params_text AS params_text,
               r.owner_qn AS owner_qn, r.export AS export,
               r.routine_type AS routine_type
    """, {"p": PROJECT_NAME})

    if rows:
        existing_keys = store.get_all_keys("routines")
        current_keys = set()
        to_embed_rows = []
        to_embed_texts = []

        for r in rows:
            key = r["id"]
            current_keys.add(key)
            parts = [_split_camel_case(r.get("name", ""))]
            if r.get("doc_description"):
                parts.append(r["doc_description"])
            else:
                # Auto-generate description from context
                owner_qn = r.get("owner_qn", "")
                owner = owner_qn.split("/")
                rtype = r.get("routine_type", "Procedure")
                if len(owner) >= 4:
                    auto = f"{rtype} {_split_camel_case(r.get('name', ''))}. Модуль: {_split_camel_case(owner[3])}"
                    params = r.get("params_text", "")
                    if params:
                        auto += f". Параметры: {params}"
                    parts.append(auto)
            if r.get("signature"):
                parts.append(r["signature"])
            if r.get("owner_qn"):
                owner = r["owner_qn"].split("/")
                if len(owner) >= 4:
                    parts.append(f"{owner[2]} {owner[3]}")
            text = " | ".join(parts)
            th = text_hash(text)

            # Skip if unchanged
            if store.get_hash(key) == th:
                continue
            to_embed_rows.append((r, th))
            to_embed_texts.append(text)

        # Remove deleted routines
        for old_key in existing_keys - current_keys:
            store.remove(old_key)

        if to_embed_texts:
            log.info("Embedding %d routines (%d unchanged, %d removed)...",
                     len(to_embed_texts), len(rows) - len(to_embed_texts),
                     len(existing_keys - current_keys))
            vectors = embedder.embed_passages(to_embed_texts, batch_size=EMBEDDING_BATCH_SIZE)
            for (r, th), text, vec in zip(to_embed_rows, to_embed_texts, vectors):
                store.add(r["id"], "routines", vec, {
                    "name": r.get("name", ""),
                    "signature": r.get("signature", ""),
                    "description": r.get("doc_description", ""),
                    "owner_qn": r.get("owner_qn", ""),
                    "export": r.get("export", False),
                    "routine_type": r.get("routine_type", ""),
                }, text_hash=th, search_text=text)
            log.info("Embedded %d routines", len(to_embed_texts))
        else:
            log.info("All %d routine embeddings up to date", len(rows))

    # --- Embed metadata objects ---
    # category + name (CamelCase split) + synonym + comment + top-5 attribute names
    obj_rows = run_query("""
        MATCH (mo:MetadataObject {project_name: $p})
        RETURN mo.name AS name, mo.category_name AS category,
               mo.Synonym AS synonym, mo.Comment AS comment,
               mo.qualified_name AS qn
    """, {"p": PROJECT_NAME})

    # Pre-fetch: attribute names (max 5), USED_IN refs (max 5), enum values (max 5)
    attr_map = {}
    attr_rows = run_query("""
        MATCH (mo:MetadataObject {project_name: $p})-[:HAS_ATTRIBUTE]->(a:Attribute)
        RETURN mo.qualified_name AS qn, a.name AS name
    """, {"p": PROJECT_NAME})
    for ar in attr_rows:
        lst = attr_map.setdefault(ar["qn"], [])
        if len(lst) < 5:
            lst.append(ar["name"])

    ref_map = {}
    ref_rows = run_query("""
        MATCH (other:MetadataObject)-[:USED_IN]->(mo:MetadataObject {project_name: $p})
        RETURN mo.qualified_name AS qn, other.name AS name
    """, {"p": PROJECT_NAME})
    for rr in ref_rows:
        lst = ref_map.setdefault(rr["qn"], [])
        if len(lst) < 5:
            lst.append(rr["name"])

    enum_map = {}
    enum_rows = run_query("""
        MATCH (mo:MetadataObject {project_name: $p})-[:HAS_ENUM_VALUE]->(ev:EnumValue)
        RETURN mo.qualified_name AS qn, ev.name AS name
    """, {"p": PROJECT_NAME})
    for er in enum_rows:
        lst = enum_map.setdefault(er["qn"], [])
        if len(lst) < 5:
            lst.append(er["name"])

    if obj_rows:
        existing_keys = store.get_all_keys("objects")
        current_keys = set()
        to_embed_rows = []
        to_embed_texts = []

        for o in obj_rows:
            key = o["qn"]
            current_keys.add(key)
            parts = []
            if o.get("category"):
                parts.append(o["category"])
            name = o.get("name", "")
            name_split = _split_camel_case(name)
            parts.append(name_split)
            if o.get("synonym"):
                parts.append(o["synonym"])
            if o.get("comment"):
                parts.append(o["comment"])
            # Top-5 attribute names
            obj_attrs = attr_map.get(key, [])
            if obj_attrs:
                parts.append("Реквизиты: " + ", ".join(_split_camel_case(a) for a in obj_attrs))
            # Enum values (for Перечисления)
            obj_enums = enum_map.get(key, [])
            if obj_enums:
                parts.append("Значения: " + ", ".join(_split_camel_case(e) for e in obj_enums))
            # Who uses this object (max 5)
            obj_refs = ref_map.get(key, [])
            if obj_refs:
                parts.append("Используется в: " + ", ".join(obj_refs))
            text = " | ".join(parts)
            th = text_hash(text)

            if store.get_hash(key) == th:
                continue
            to_embed_rows.append((o, th))
            to_embed_texts.append(text)

        for old_key in existing_keys - current_keys:
            store.remove(old_key)

        if to_embed_texts:
            log.info("Embedding %d metadata objects (%d unchanged, %d removed)...",
                     len(to_embed_texts), len(obj_rows) - len(to_embed_texts),
                     len(existing_keys - current_keys))
            vectors = embedder.embed_passages(to_embed_texts, batch_size=EMBEDDING_BATCH_SIZE)
            for (o, th), text, vec in zip(to_embed_rows, to_embed_texts, vectors):
                store.add(o["qn"], "objects", vec, {
                    "name": o.get("name", ""),
                    "category": o.get("category", ""),
                    "synonym": o.get("synonym", ""),
                    "comment": o.get("comment", ""),
                }, text_hash=th, search_text=text)
            log.info("Embedded %d metadata objects", len(to_embed_texts))
        else:
            log.info("All %d object embeddings up to date", len(obj_rows))

    log.info("Embedding complete: %d routines, %d objects in vector store",
             store.count("routines"), store.count("objects"))


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
        # Still initialize embedding service (models + sqlite-vec)
        if ENABLE_EMBEDDING:
            from app.services.embedding import init
            init(EMBEDDING_MODEL_PATH, EMBEDDING_DB_PATH,
                 reranker_path=RERANKER_MODEL_PATH,
                 query_prefix=EMBEDDING_QUERY_PREFIX,
                 passage_prefix=EMBEDDING_PASSAGE_PREFIX)
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
    routines = []
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

    # 7b. Build USED_IN from BSL code references
    if routines:
        _build_code_used_in(routines)

    # 7c. Build DO_MOVEMENTS_IN from document module code
    if routines:
        _build_movements(routines)

    # 8. Build embeddings (optional)
    if ENABLE_EMBEDDING:
        _build_embeddings()

    # Final stats
    stats = run_query("""
        MATCH (n) WHERE n.project_name = $name
        RETURN labels(n)[0] AS label, count(n) AS cnt
        ORDER BY cnt DESC
    """, {"name": PROJECT_NAME})
    log.info("Load complete. Stats:")
    for s in stats:
        log.info("  %s: %d", s["label"], s["cnt"])
