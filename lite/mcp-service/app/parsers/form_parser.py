"""Parser for 1C Form.xml files.

Extracts: form controls, form attributes, events, bindings.
"""

import logging
import xml.etree.ElementTree as ET
from pathlib import Path

log = logging.getLogger(__name__)

NS = {
    "": "http://v8.1c.ru/8.3/xcf/logform",
    "v8": "http://v8.1c.ru/8.1/data/core",
    "cfg": "http://v8.1c.ru/8.1/data/enterprise/current-config",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}

# Default namespace prefix for XPath
D = "{http://v8.1c.ru/8.3/xcf/logform}"
V8 = "{http://v8.1c.ru/8.1/data/core}"

CONTROL_TAGS = {
    "InputField", "LabelField", "LabelDecoration", "ButtonDecoration",
    "UsualGroup", "Pages", "Page", "Table", "UsualButton",
    "CommandBar", "AutoCommandBar", "Popup",
    "SearchStringAddition", "ViewStatusAddition", "SearchControlAddition",
    "CheckBoxField", "RadioButtonField", "SpreadSheetDocumentField",
    "TextDocumentField", "HTMLDocumentField", "PictureField",
    "FormattedDocumentField", "PlannerField", "ChartField",
    "DendrogramField", "MapField", "CalendarField",
    "ProgressBarField", "TrackBarField", "ColumnGroup",
}


def _get_text(elem, tag):
    """Get text content of a direct child element."""
    child = elem.find(f"{D}{tag}")
    if child is not None and child.text:
        return child.text.strip()
    # Try localized v8:item
    child = elem.find(f"{D}{tag}/{V8}item/{V8}content")
    if child is not None and child.text:
        return child.text.strip()
    return ""


def _extract_controls(elem, parent_qn: str, controls: list):
    """Recursively extract controls from ChildItems."""
    for child in elem:
        tag = child.tag.replace(D, "").replace("{http://v8.1c.ru/8.3/xcf/logform}", "")
        if tag in CONTROL_TAGS:
            name = child.get("name", "")
            cid = child.get("id", "")
            data_path = _get_text(child, "DataPath")
            qn = f"{parent_qn}/{name}" if name else parent_qn

            controls.append({
                "name": name,
                "id": cid,
                "type": tag,
                "data_path": data_path,
                "qualified_name": qn,
            })

            # Recurse into ChildItems
            child_items = child.find(f"{D}ChildItems")
            if child_items is not None:
                _extract_controls(child_items, qn, controls)

            # Also check for ContextMenu, ExtendedTooltip as sub-elements
            for sub_tag in ("ContextMenu", "ExtendedTooltip"):
                sub = child.find(f"{D}{sub_tag}")
                if sub is not None:
                    sub_name = sub.get("name", sub_tag)
                    controls.append({
                        "name": sub_name,
                        "id": sub.get("id", ""),
                        "type": sub_tag,
                        "data_path": "",
                        "qualified_name": f"{qn}/{sub_name}",
                    })


def _extract_events(elem, parent_qn: str, events: list):
    """Extract events from form elements."""
    event_elem = elem.find(f"{D}Events")
    if event_elem is not None:
        for ev in event_elem:
            tag = ev.tag.replace(D, "")
            handler = ev.text.strip() if ev.text else ""
            if handler:
                events.append({
                    "name": tag,
                    "handler": handler,
                    "qualified_name": f"{parent_qn}/Event/{tag}",
                })
    # Recurse
    child_items = elem.find(f"{D}ChildItems")
    if child_items is not None:
        for child in child_items:
            name = child.get("name", "")
            child_qn = f"{parent_qn}/{name}" if name else parent_qn
            _extract_events(child, child_qn, events)


def parse_form_xml(filepath: str, owner_qn: str) -> dict:
    """Parse a Form.xml and return controls, attributes, events."""
    try:
        tree = ET.parse(filepath)
    except ET.ParseError as e:
        log.warning("XML parse error in %s: %s", filepath, e)
        return {"controls": [], "attributes": [], "events": []}

    root = tree.getroot()

    controls = []
    events = []
    attributes = []

    # Extract controls from top-level ChildItems
    child_items = root.find(f"{D}ChildItems")
    if child_items is not None:
        _extract_controls(child_items, owner_qn, controls)

    # Extract events
    _extract_events(root, owner_qn, events)

    # Extract form attributes
    attrs_elem = root.find(f"{D}Attributes")
    if attrs_elem is not None:
        for attr in attrs_elem:
            if attr.tag == f"{D}Attribute":
                name = attr.get("name", "")
                aid = attr.get("id", "")
                attributes.append({
                    "name": name,
                    "id": aid,
                    "qualified_name": f"{owner_qn}/Attribute/{name}",
                })

    return {
        "controls": controls,
        "attributes": attributes,
        "events": events,
    }


def scan_forms(code_dir: str, project_name: str, config_name: str) -> list[dict]:
    """Scan all Form.xml files and return parsed form data."""
    results = []
    code_path = Path(code_dir)

    form_files = list(code_path.rglob("Ext/Form.xml"))
    log.info("Found %d Form.xml files", len(form_files))

    for form_file in form_files:
        rel = form_file.relative_to(code_path)
        parts = list(rel.parts)
        # Build owner QN: project/config/Category/Object/Form/FormName
        owner_parts = [project_name, config_name]
        if len(parts) >= 4:
            owner_parts.extend(parts[:2])  # Category, Object
            if "Forms" in parts:
                idx = parts.index("Forms")
                if idx + 1 < len(parts):
                    owner_parts.append("Form")
                    owner_parts.append(parts[idx + 1])

        owner_qn = "/".join(owner_parts)

        try:
            form_data = parse_form_xml(str(form_file), owner_qn)
            form_data["owner_qn"] = owner_qn
            form_data["file_path"] = str(form_file)
            results.append(form_data)
        except Exception as e:
            log.warning("Error parsing form %s: %s", form_file, e)

    log.info("Parsed %d forms", len(results))
    return results
