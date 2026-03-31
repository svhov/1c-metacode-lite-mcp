"""Parser for Roles/*/Ext/Rights.xml — role access rights."""

import logging
import xml.etree.ElementTree as ET
from pathlib import Path

log = logging.getLogger(__name__)

NS_ROLES = "http://v8.1c.ru/8.2/roles"
D = f"{{{NS_ROLES}}}"


def parse_rights_xml(filepath: str, role_name: str) -> list[dict]:
    """Parse Rights.xml and return list of access grants."""
    try:
        tree = ET.parse(filepath)
    except ET.ParseError as e:
        log.warning("XML parse error in %s: %s", filepath, e)
        return []

    root = tree.getroot()
    grants = []

    for obj_elem in root.iter(f"{D}object"):
        target_name = ""
        rights = {}

        name_elem = obj_elem.find(f"{D}name")
        if name_elem is not None and name_elem.text:
            target_name = name_elem.text.strip()

        for right_elem in obj_elem.iter(f"{D}right"):
            right_name_elem = right_elem.find(f"{D}name")
            right_val_elem = right_elem.find(f"{D}value")
            if right_name_elem is not None and right_val_elem is not None:
                rname = (right_name_elem.text or "").strip()
                rval = (right_val_elem.text or "").strip().lower() == "true"
                if rval:
                    rights[rname] = True

        if target_name and rights:
            grants.append({
                "role_name": role_name,
                "target_name": target_name,
                "rights": rights,
            })

    return grants


def scan_roles(code_dir: str, project_name: str, config_name: str) -> list[dict]:
    """Scan all Rights.xml files under Roles/."""
    all_grants = []
    roles_dir = Path(code_dir) / "Roles"

    if not roles_dir.exists():
        return all_grants

    rights_files = list(roles_dir.rglob("Ext/Rights.xml"))
    log.info("Found %d Rights.xml files", len(rights_files))

    for rights_file in rights_files:
        rel = rights_file.relative_to(roles_dir)
        role_name = rel.parts[0] if rel.parts else ""

        try:
            grants = parse_rights_xml(str(rights_file), role_name)
            all_grants.extend(grants)
        except Exception as e:
            log.warning("Error parsing %s: %s", rights_file, e)

    log.info("Parsed %d role grants", len(all_grants))
    return all_grants
