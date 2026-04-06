"""Parser for .bsl files — 1C:Enterprise script language.

Extracts procedures/functions with metadata:
- name, type (Procedure/Function), export flag
- directive (&НаСервере, &НаКлиенте, etc.)
- parameters (name, default value)
- doc comments (description, params, return)
- body text
- line number
- region/area path
"""

import logging
import os
import re
from pathlib import Path

log = logging.getLogger(__name__)

# Regex for procedure/function declaration
RE_ROUTINE_START = re.compile(
    r"^\s*(Процедура|Функция|Procedure|Function)\s+"
    r"(\w+)\s*\(([^)]*)\)\s*(Экспорт|Export)?\s*$",
    re.IGNORECASE,
)

RE_ROUTINE_END = re.compile(
    r"^\s*(КонецПроцедуры|КонецФункции|EndProcedure|EndFunction)\s*$",
    re.IGNORECASE,
)

RE_DIRECTIVE = re.compile(
    r"^\s*&(НаСервере|НаКлиенте|НаСервереБезКонтекста|НаКлиентеНаСервереБезКонтекста|"
    r"НаКлиентеНаСервере|AtServer|AtClient|AtServerNoContext|AtClientAtServerNoContext|AtClientAtServer)\s*$",
    re.IGNORECASE,
)

RE_REGION = re.compile(r"^\s*#(?:Область|Region)\s+(\S+)", re.IGNORECASE)
RE_ENDREGION = re.compile(r"^\s*#(?:КонецОбласти|EndRegion)", re.IGNORECASE)

RE_CALL = re.compile(r"\b(\w+)\s*\(")

# Regex for metadata object references in BSL code (e.g. Справочники.Контрагенты)
RE_METADATA_REF = re.compile(
    r"\b("
    r"Справочники|Документы|Перечисления|"
    r"РегистрыСведений|РегистрыНакопления|РегистрыБухгалтерии|РегистрыРасчета|"
    r"ПланыВидовХарактеристик|ПланыСчетов|ПланыВидовРасчета|"
    r"БизнесПроцессы|Задачи|Константы|Обработки|Отчеты|"
    r"Catalogs|Documents|Enums|InformationRegisters|AccumulationRegisters|"
    r"AccountingRegisters|CalculationRegisters|ChartsOfCharacteristicTypes|"
    r"ChartsOfAccounts|ChartsOfCalculationTypes|BusinessProcesses|Tasks|"
    r"Constants|DataProcessors|Reports"
    r")\.(\w+)\b"
)

# Built-in manager methods to skip (not object names)
_BUILTIN_METHODS = frozenset({
    "создать", "создатьэлемент", "создатьгруппу", "создатьдокумент",
    "найтипокоду", "найтипонаименованию", "найтипоссылке", "найтипореквизиту",
    "выбрать", "выбратьиерархически", "получить", "получитьформу",
    "создатьнаборзаписей", "создатьменеджерзаписи",
    "пустаяссылка", "получитьссылку", "получитьмакет",
    "create", "createelement", "creategroup", "createdocument",
    "findbycode", "findbyname", "findbyattribute",
    "select", "get", "getform", "emptyref", "getref", "gettemplate",
    "createrecordset", "createrecordmanager",
})

# SSL API markers in doc comments
SSL_API_MARKERS = [
    "// СМ. ТАКЖЕ", "// см. также",
    "// Описание:", "// Параметры:",
    "// Возвращаемое значение:",
]


def _detect_encoding(filepath: str) -> str:
    with open(filepath, "rb") as f:
        bom = f.read(4)
    if bom[:2] == b"\xff\xfe":
        return "utf-16-le"
    if bom[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    return "utf-8"


def _read_bsl(filepath: str) -> list[str]:
    enc = _detect_encoding(filepath)
    with open(filepath, encoding=enc, errors="replace") as f:
        return f.readlines()


def _parse_doc_comment(lines: list[str], end_idx: int) -> dict:
    """Extract doc comment block above a routine declaration."""
    doc_lines = []
    i = end_idx - 1
    while i >= 0:
        stripped = lines[i].strip()
        if stripped.startswith("//"):
            doc_lines.insert(0, stripped[2:].strip())
            i -= 1
        else:
            break
    if not doc_lines:
        return {}

    full_text = "\n".join(doc_lines)
    result = {"doc_description": "", "doc_params_text": "", "doc_return_text": ""}

    # Split into sections
    sections = re.split(r"\n(?=Параметры:|Возвращаемое значение:|Parameters:|Returns:)", full_text)
    for section in sections:
        lower = section.strip().lower()
        if lower.startswith("параметры:") or lower.startswith("parameters:"):
            result["doc_params_text"] = section.strip()
        elif lower.startswith("возвращаемое значение:") or lower.startswith("returns:"):
            result["doc_return_text"] = section.strip()
        else:
            if not result["doc_description"]:
                result["doc_description"] = section.strip()

    return result


def _parse_params(params_str: str) -> list[dict]:
    """Parse parameter list from declaration."""
    params = []
    if not params_str.strip():
        return params
    for part in params_str.split(","):
        part = part.strip()
        if "=" in part:
            name, default = part.split("=", 1)
            params.append({"name": name.strip(), "default": default.strip()})
        else:
            params.append({"name": part.strip(), "default": None})
    return params


def _extract_metadata_refs(body: str) -> list[tuple[str, str]]:
    """Extract metadata object references from BSL body.

    Returns list of (category, object_name) tuples.
    E.g., ("Справочники", "Контрагенты") from ``Справочники.Контрагенты.НайтиПоКоду(...)``.
    """
    refs = set()
    for m in RE_METADATA_REF.finditer(body):
        category = m.group(1)
        obj_name = m.group(2)
        if obj_name.lower() in _BUILTIN_METHODS:
            continue
        refs.add((category, obj_name))
    return list(refs)


def _extract_calls(body: str, own_name: str) -> list[str]:
    """Extract function/procedure call names from body."""
    calls = set()
    for m in RE_CALL.finditer(body):
        name = m.group(1)
        # Skip keywords and self
        if name.lower() in (
            "если", "пока", "для", "возврат", "новый", "выполнить",
            "if", "while", "for", "return", "new", "execute",
            own_name.lower(),
        ):
            continue
        calls.add(name)
    return list(calls)


def parse_bsl_file(filepath: str, owner_qn: str, project_name: str, config_name: str) -> list[dict]:
    """Parse a single .bsl file and return list of routine dicts."""
    lines = _read_bsl(filepath)
    routines = []
    region_stack = []
    current_directive = None

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Track regions
        m_region = RE_REGION.match(stripped)
        if m_region:
            region_stack.append(m_region.group(1))
            i += 1
            continue
        if RE_ENDREGION.match(stripped):
            if region_stack:
                region_stack.pop()
            i += 1
            continue

        # Track directive
        m_dir = RE_DIRECTIVE.match(stripped)
        if m_dir:
            current_directive = m_dir.group(1)
            i += 1
            continue

        # Match routine start
        m = RE_ROUTINE_START.match(stripped)
        if m:
            routine_type = m.group(1)
            name = m.group(2)
            params_str = m.group(3)
            export = bool(m.group(4))

            start_line = i + 1  # 1-based
            doc = _parse_doc_comment(lines, i)
            params = _parse_params(params_str)

            # Collect body until end
            body_lines = [line.rstrip("\r\n")]
            j = i + 1
            while j < len(lines):
                body_lines.append(lines[j].rstrip("\r\n"))
                if RE_ROUTINE_END.match(lines[j].strip()):
                    break
                j += 1

            body = "\n".join(body_lines)
            calls = _extract_calls(body, name)
            metadata_refs = _extract_metadata_refs(body)

            # Determine if SSL API
            is_ssl_api = export and any(
                marker.lower() in "\n".join(lines[max(0, i - 15):i]).lower()
                for marker in SSL_API_MARKERS
            )

            # Build signature
            if routine_type.lower() in ("функция", "function"):
                rtype = "Function"
            else:
                rtype = "Procedure"

            params_text = ", ".join(
                p["name"] + (f" = {p['default']}" if p["default"] else "")
                for p in params
            )
            signature = f"{rtype} {name}({params_text})"
            if export:
                signature += " Export"

            routine_id = f"{owner_qn}.{name}"
            area_path = "/".join(region_stack) if region_stack else ""

            routines.append({
                "id": routine_id,
                "name": name,
                "routine_type": rtype,
                "directive": current_directive or "",
                "export": export,
                "is_ssl_api": is_ssl_api,
                "params": params,
                "params_text": params_text,
                "params_json_str": str(params),
                "param_names": [p["name"] for p in params],
                "signature": signature,
                "body": body,
                "line": start_line,
                "file_path": filepath,
                "area_path": area_path,
                "owner_qn": owner_qn,
                "project_name": project_name,
                "config_name": config_name,
                "calls": calls,
                "metadata_refs": metadata_refs,
                **doc,
            })

            current_directive = None
            i = j + 1
            continue

        # Reset directive if we hit a non-empty non-comment line
        if stripped and not stripped.startswith("//"):
            current_directive = None

        i += 1

    return routines


def scan_bsl_files(code_dir: str, project_name: str, config_name: str) -> list[dict]:
    """Scan all .bsl files under code_dir and return all routines."""
    all_routines = []
    code_path = Path(code_dir)

    if not code_path.exists():
        log.warning("Code directory not found: %s", code_dir)
        return all_routines

    bsl_files = list(code_path.rglob("*.bsl"))
    log.info("Found %d .bsl files in %s", len(bsl_files), code_dir)

    for bsl_file in bsl_files:
        # Determine owner from path
        rel = bsl_file.relative_to(code_path)
        parts = list(rel.parts)

        # Build owner qualified name
        # e.g., CommonModules/КоннекторHTTP/Ext/Module.bsl -> project/config/ОбщиеМодули/КоннекторHTTP
        # e.g., Catalogs/Файлы/Forms/ФормаСписка/Ext/Form/Module.bsl -> project/config/Справочники/Файлы/Form/ФормаСписка
        owner_parts = [project_name, config_name]
        if len(parts) >= 2:
            # Category (English dir name -> use as is)
            owner_parts.append(parts[0])
            # Object name
            owner_parts.append(parts[1])
            # If there's Forms/FormName deeper
            if len(parts) >= 4 and parts[2] == "Forms":
                owner_parts.append("Form")
                owner_parts.append(parts[3])

        owner_qn = "/".join(owner_parts)

        try:
            routines = parse_bsl_file(str(bsl_file), owner_qn, project_name, config_name)
            all_routines.extend(routines)
        except Exception as e:
            log.warning("Error parsing %s: %s", bsl_file, e)

    log.info("Parsed %d routines from %d files", len(all_routines), len(bsl_files))
    return all_routines
