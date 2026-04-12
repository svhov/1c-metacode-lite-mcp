"""Parser for ОтчетПоКонфигурации.txt — 1C configuration metadata report.

File format:
- UTF-16 LE with BOM (or UTF-8 with BOM)
- Tab-indented hierarchy
- Root: "\t- Конфигурации.ИмяКонфигурации"
- Objects: "\t\t- Категория.ИмяОбъекта"
- Children: "\t\t\t- Категория.ИмяОбъекта.ДочерняяКатегория.ДочернееИмя"
- Properties: "\t\t\tКлюч: \"Значение\""
"""

import logging
import os
import re

log = logging.getLogger(__name__)

# Top-level categories (plural Russian -> canonical)
CATEGORY_MAP = {
    "Подсистемы": "Subsystems",
    "ОбщиеМодули": "CommonModules",
    "ОбщиеФормы": "CommonForms",
    "ОбщиеКоманды": "CommonCommands",
    "ОбщиеКартинки": "CommonPictures",
    "ОбщиеШаблоны": "CommonTemplates",
    "Роли": "Roles",
    "Константы": "Constants",
    "Справочники": "Catalogs",
    "Документы": "Documents",
    "ЖурналыДокументов": "DocumentJournals",
    "Перечисления": "Enums",
    "Отчеты": "Reports",
    "Обработки": "DataProcessors",
    "ПланыВидовХарактеристик": "ChartsOfCharacteristicTypes",
    "ПланыСчетов": "ChartsOfAccounts",
    "ПланыВидовРасчета": "ChartsOfCalculationTypes",
    "РегистрыСведений": "InformationRegisters",
    "РегистрыНакопления": "AccumulationRegisters",
    "РегистрыБухгалтерии": "AccountingRegisters",
    "РегистрыРасчета": "CalculationRegisters",
    "БизнесПроцессы": "BusinessProcesses",
    "Задачи": "Tasks",
    "HTTPСервисы": "HTTPServices",
    "WebСервисы": "WebServices",
    "ЭлементыСтиля": "StyleItems",
    "Языки": "Languages",
    "РегламентныеЗадания": "ScheduledJobs",
    "ОпределяемыеТипы": "DefinedTypes",
    "ПакетыXDTO": "XDTOPackages",
    "ФункциональныеОпции": "FunctionalOptions",
    "ПараметрыФункциональныхОпций": "FunctionalOptionsParameters",
    "ХранилищаНастроек": "SettingsStorages",
    "Последовательности": "Sequences",
    "ВнешниеИсточникиДанных": "ExternalDataSources",
    "Интерфейсы": "Interfaces",
}

# Child categories within objects
CHILD_CATEGORY_MAP = {
    "Реквизиты": "Attribute",
    "Ресурсы": "Resource",
    "Измерения": "Dimension",
    "ТабличныеЧасти": "TabularPart",
    "Формы": "Form",
    "Макеты": "Layout",
    "Команды": "Command",
    "ЗначенияПеречисления": "EnumValue",
    "ПризнакиУчета": "AccountingFlag",
    "ПризнакиУчетаСубконто": "DimensionAccountingFlag",
    "ГрафыОтбора": "JournalGraph",
    "ШаблоныURL": "UrlTemplate",
    "Методы": "UrlMethod",
    "Характеристики": "Characteristic",
    "Колонки": "Attribute",
    "Графы": "Resource",
    "Подсистемы": "Subsystem",
}


def _detect_encoding(filepath: str) -> str:
    with open(filepath, "rb") as f:
        bom = f.read(4)
    if bom[:2] == b"\xff\xfe":
        return "utf-16"
    if bom[:3] == b"\xef\xbb\xbf":
        return "utf-8-sig"
    return "utf-8"


def _count_tabs(line: str) -> int:
    count = 0
    for ch in line:
        if ch == "\t":
            count += 1
        else:
            break
    return count


class ParsedConfig:
    def __init__(self):
        self.config_name: str = ""
        self.config_props: dict[str, str] = {}
        self.categories: list[dict] = []
        self.objects: list[dict] = []

    @property
    def object_count(self):
        return len(self.objects)


def parse_metadata_report(filepath: str) -> ParsedConfig:
    """Parse ОтчетПоКонфигурации.txt and return structured metadata."""
    log.info("Parsing metadata report: %s", filepath)

    enc = _detect_encoding(filepath)
    with open(filepath, encoding=enc, errors="replace") as f:
        lines = f.readlines()
    log.info("Read %d lines (encoding: %s)", len(lines), enc)

    result = ParsedConfig()

    # State tracking
    current_object = None  # Current top-level metadata object
    current_child = None   # Current child (attribute, form, etc.)
    current_child_type = None
    current_sub_child = None  # Deepest child (e.g., TP attribute)

    for i, raw_line in enumerate(lines):
        line = raw_line.rstrip("\r\n")
        depth = _count_tabs(line)
        stripped = line.strip()

        if not stripped:
            continue

        # --- Dash-prefixed header lines ---
        if stripped.startswith("- "):
            dotpath = stripped[2:].strip()
            parts = dotpath.split(".")

            if depth == 1:
                # Root: "- Конфигурации.АМЕ"
                if len(parts) >= 2:
                    result.config_name = parts[1]
                continue

            if depth == 2:
                # Top-level object: "- Справочники.АдреснаяКнига"
                if len(parts) >= 2:
                    cat_name = parts[0]
                    obj_name = parts[1]
                    current_object = {
                        "name": obj_name,
                        "category": cat_name,
                        "category_en": CATEGORY_MAP.get(cat_name, cat_name),
                        "properties": {},
                        "children": {},
                    }
                    result.objects.append(current_object)
                    current_child = None
                    current_child_type = None
                continue

            if depth >= 3 and current_object:
                # Child: "- Справочники.АдреснаяКнига.Формы.ФормаСписка"
                # or deeper: "- Справочники.X.ТабличныеЧасти.Y.Реквизиты.Z"
                # Find the child category and name from the dotpath
                # Skip first 2 parts (top-level category + object name)
                child_parts = parts[2:]
                if len(child_parts) >= 2:
                    child_cat = child_parts[0]  # e.g., "Формы", "Реквизиты"
                    child_name = child_parts[1]
                    child_type = CHILD_CATEGORY_MAP.get(child_cat, child_cat)

                    child_entry = {
                        "name": child_name,
                        "type": child_type,
                        "properties": {},
                        "children": [],
                    }
                    current_object.setdefault("children", {}).setdefault(
                        child_type, []
                    ).append(child_entry)
                    current_child = child_entry
                    current_child_type = child_type
                    current_sub_child = None

                    # Deeper nesting (e.g., TabularPart attributes)
                    if len(child_parts) >= 4:
                        # e.g., ТабличныеЧасти.Y.Реквизиты.Z
                        sub_cat = child_parts[2]
                        sub_name = child_parts[3]
                        sub_type = CHILD_CATEGORY_MAP.get(sub_cat, sub_cat)
                        sub_entry = {"name": sub_name, "properties": {}}
                        child_entry.setdefault("children", []).append(sub_entry)
                        current_sub_child = sub_entry
                continue

        # --- Property lines (no dash prefix) ---
        # Format: "Ключ: \"Значение\"" or "Ключ:"
        m = re.match(r'^(.+?):\s*"(.*?)"', stripped)
        if m:
            key = m.group(1).strip()
            val = m.group(2).strip()

            if depth == 2 and not current_object:
                # Config-level property
                result.config_props[key] = val
                if key == "Имя":
                    result.config_name = val
            elif depth == 3 and current_object and not current_child:
                # Object-level property
                current_object["properties"][key] = val
            elif depth >= 5 and current_sub_child:
                # Sub-child property (e.g., TP attribute)
                current_sub_child["properties"][key] = val
            elif depth >= 4 and current_child:
                # Child-level property
                current_child["properties"][key] = val
            continue

        # Collection header (e.g., "Владельцы:", "Тип:", "ОсновныеРоли:")
        m2 = re.match(r'^(.+?):\s*$', stripped)
        if m2:
            coll_key = m2.group(1).strip()
            # Peek ahead to collect quoted values for this collection
            coll_values = []
            j = i + 1
            while j < len(lines):
                next_line = lines[j].rstrip("\r\n")
                next_stripped = next_line.strip()
                next_depth = _count_tabs(next_line)
                if next_depth > depth and next_stripped.startswith('"') and next_stripped.endswith('"'):
                    coll_values.append(next_stripped.strip('"').strip())
                    j += 1
                else:
                    break
            # Store "Тип:" collection as property (join multiple types)
            if coll_key == "Тип" and coll_values:
                type_val = ", ".join(coll_values)
                if depth >= 5 and current_sub_child:
                    current_sub_child["properties"]["Тип"] = type_val
                elif depth >= 4 and current_child:
                    current_child["properties"]["Тип"] = type_val
                elif depth == 3 and current_object and not current_child:
                    current_object["properties"]["Тип"] = type_val
            # Store "Состав:" collection for subsystems (list of member objects)
            elif coll_key == "Состав" and coll_values and current_object:
                current_object["properties"]["Состав"] = coll_values
            continue

        # Quoted collection values (e.g., '"Роль.АМЕ_ОсновнаяРоль"')
        if stripped.startswith('"') and stripped.endswith('"'):
            continue

    # Build categories list
    seen_cats = set()
    for obj in result.objects:
        cat = obj["category"]
        if cat and cat not in seen_cats:
            seen_cats.add(cat)
            result.categories.append({
                "name": cat,
                "name_en": CATEGORY_MAP.get(cat, cat),
            })

    log.info(
        "Parsed config: %s, %d objects in %d categories",
        result.config_name,
        len(result.objects),
        len(result.categories),
    )
    return result
