"""Parser for ConfigDumpInfo.xml — GUID to path mapping."""

import logging
import xml.etree.ElementTree as ET

log = logging.getLogger(__name__)

NS = "http://v8.1c.ru/8.3/xcf/dumpinfo"
D = f"{{{NS}}}"


def parse_config_dump(filepath: str) -> dict[str, str]:
    """Parse ConfigDumpInfo.xml and return {name: uuid} mapping."""
    try:
        tree = ET.parse(filepath)
    except ET.ParseError as e:
        log.warning("XML parse error in %s: %s", filepath, e)
        return {}

    root = tree.getroot()
    guid_map = {}

    def _walk(elem):
        name = elem.get("name", "")
        uid = elem.get("id", "")
        if name and uid:
            guid_map[name] = uid
        for child in elem:
            if child.tag == f"{D}Metadata":
                _walk(child)

    versions = root.find(f"{D}ConfigVersions")
    if versions is not None:
        for meta in versions:
            if meta.tag == f"{D}Metadata":
                _walk(meta)

    log.info("Loaded %d GUID entries from %s", len(guid_map), filepath)
    return guid_map
