"""Parser for Predefined.xml files — predefined catalog/chart items."""

import logging
import xml.etree.ElementTree as ET
from pathlib import Path

log = logging.getLogger(__name__)

NS_PREDEF = "http://v8.1c.ru/8.3/xcf/predef"
D = f"{{{NS_PREDEF}}}"


def parse_predefined_xml(filepath: str, owner_qn: str) -> list[dict]:
    """Parse a Predefined.xml and return list of predefined items."""
    try:
        tree = ET.parse(filepath)
    except ET.ParseError as e:
        log.warning("XML parse error in %s: %s", filepath, e)
        return []

    root = tree.getroot()
    items = []

    for item_elem in root.iter(f"{D}Item"):
        item_id = item_elem.get("id", "")
        name = ""
        description = ""
        is_folder = False
        code = ""

        for child in item_elem:
            tag = child.tag.replace(D, "")
            text = (child.text or "").strip()
            if tag == "Name":
                name = text
            elif tag == "Description":
                description = text
            elif tag == "IsFolder":
                is_folder = text.lower() == "true"
            elif tag == "Code":
                code = text

        if name:
            items.append({
                "id": item_id,
                "name": name,
                "description": description,
                "is_folder": is_folder,
                "code": code,
                "owner_qn": owner_qn,
                "qualified_name": f"{owner_qn}/Predefined/{name}",
            })

    return items


def scan_predefined(code_dir: str, project_name: str, config_name: str) -> list[dict]:
    """Scan all Predefined.xml files."""
    all_items = []
    code_path = Path(code_dir)

    pred_files = list(code_path.rglob("Ext/Predefined.xml"))
    log.info("Found %d Predefined.xml files", len(pred_files))

    for pred_file in pred_files:
        rel = pred_file.relative_to(code_path)
        parts = list(rel.parts)
        owner_parts = [project_name, config_name]
        if len(parts) >= 2:
            owner_parts.extend(parts[:2])
        owner_qn = "/".join(owner_parts)

        try:
            items = parse_predefined_xml(str(pred_file), owner_qn)
            all_items.extend(items)
        except Exception as e:
            log.warning("Error parsing %s: %s", pred_file, e)

    log.info("Parsed %d predefined items", len(all_items))
    return all_items
