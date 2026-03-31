# Plan: Lightweight 1C Metacode MCP

## Цель

Заменить текущий стек (roctup/1c-mcp-metacode + Neo4j 5.26) на легковесное решение с теми же MCP-тулами, но значительно меньшим потреблением RAM.

## Текущее потребление

| Компонент | RAM |
|-----------|-----|
| Neo4j 5.26 (JVM, heap 1GB) | ~1.2-1.5 GB |
| MCP-сервис x5 (Python, pyinstaller) | ~200-400 MB каждый |
| **Итого** | **~2.5-3.5 GB** |

## Целевое потребление

| Компонент | RAM |
|-----------|-----|
| Memgraph | ~200-400 MB |
| MCP-сервис x5 (Python, FastMCP) | ~50-100 MB каждый |
| **Итого** | **~0.5-1 GB** |

---

## Архитектура замены

```
                    Memgraph (bolt:7687)
                         |
        +--------+-------+-------+--------+
        |        |       |       |        |
    erp_main  erp_ame  ssl3  do_main  do_ame
     :6001    :6002   :6003  :6004   :6005
        (Python FastMCP, SSE transport)
```

Та же архитектура: общая графовая БД + отдельный MCP-сервис на каждый проект.

---

## Структура графа (сохраняется)

### Узлы (25 типов)

| Label | Ключевые свойства | Источник данных |
|-------|-------------------|-----------------|
| Project | name | создается при загрузке |
| Configuration | name, qualified_name, Synonym, Comment, + десятки свойств 1С | ОтчетПоКонфигурации.txt |
| MetadataCategory | name, qualified_name, project_name, config_name | ОтчетПоКонфигурации.txt |
| MetadataObject | name, qualified_name, category_name, project_name, config_name, Synonym, Comment | ОтчетПоКонфигурации.txt |
| Attribute | name, qualified_name | ОтчетПоКонфигурации.txt |
| Resource | name, qualified_name | ОтчетПоКонфигурации.txt |
| Dimension | name, qualified_name | ОтчетПоКонфигурации.txt |
| Characteristic | name, qualified_name | ОтчетПоКонфигурации.txt |
| TabularPart | name, qualified_name | ОтчетПоКонфигурации.txt |
| EnumValue | name, qualified_name | ОтчетПоКонфигурации.txt |
| PredefinedItem | name, qualified_name, + флаги | Predefined.xml |
| Form | name, qualified_name | XML формы |
| FormControl | name, qualified_name | Ext/Form.xml |
| FormAttribute | name, qualified_name | Ext/Form.xml |
| FormEvent | name, qualified_name | Ext/Form.xml |
| Command | name, qualified_name | ОтчетПоКонфигурации.txt |
| Layout | name, qualified_name | ОтчетПоКонфигурации.txt |
| Module | id, name, module_type, owner_kind, owner_name, owner_qn, path, project_name, config_name | BSL файлы |
| Routine | id, name, body, file_path, line, routine_type, directive, export, is_ssl_api, signature, params_text, doc_description, owner_qn, project_name, config_name | BSL файлы |
| EventSubscription | name, qualified_name | EventSubscriptions/*.xml |
| JournalGraph | name, qualified_name | ОтчетПоКонфигурации.txt |
| UrlTemplate | name, qualified_name | ОтчетПоКонфигурации.txt |
| UrlMethod | name, qualified_name | ОтчетПоКонфигурации.txt |
| AccountingFlag | name, qualified_name | ОтчетПоКонфигурации.txt |
| DimensionAccountingFlag | name, qualified_name | ОтчетПоКонфигурации.txt |

### Связи (основные)

| Тип | От -> К | Кол-во |
|-----|---------|--------|
| HAS_CONFIGURATION | Project -> Configuration | |
| HAS_CATEGORY | Configuration -> MetadataCategory | |
| CONTAINS_OBJECT | MetadataCategory -> MetadataObject | ~34K |
| HAS_ATTRIBUTE | MetadataObject -> Attribute | ~87K |
| HAS_CHILD | parent -> child (вложенность) | ~44K |
| USED_IN | перекрестные ссылки типов | ~43K |
| HAS_FORM | MetadataObject -> Form | ~13K |
| HAS_MODULE | MetadataObject -> Module | ~7K |
| DECLARES | Module -> Routine | ~4.4K |
| CALLS | Routine -> Routine | ~500 |
| GRANTS_ACCESS_TO | Role -> Object (права) | ~8K |
| DO_MOVEMENTS_IN | Document -> Register (движения) | ~7K |
| HAS_TABULAR_PART | MetadataObject -> TabularPart | |
| HAS_RESOURCE | MetadataObject -> Resource | |
| HAS_DIMENSION | MetadataObject -> Dimension | |
| HAS_ENUM_VALUE | MetadataObject -> EnumValue | |
| HAS_EVENT | Form -> FormEvent | |
| BINDS_TO | FormControl -> FormAttribute | |
| HAS_FORM_ATTRIBUTE | Form -> FormAttribute | |
| HAS_LAYOUT | MetadataObject -> Layout | |
| HAS_COMMAND | MetadataObject -> Command | |

---

## MCP-тулы (3 тула, 97+ операций)

### Tool 1: search_metadata
Принимает JSON `{"op": "<operation>", ...params}`. Все 97 операций:

**Объекты и категории:**
- list_objects_by_name, list_objects_by_category, list_categories, object_structure
- resolve_qn, resolve_qn_prefix, find_by_guid, get_node_properties

**Атрибуты/Ресурсы/Измерения:**
- list_attributes, list_attributes_with_type
- list_resources, list_resources_with_type
- list_dimensions, list_dimensions_with_type
- list_characteristics, list_characteristics_with_type

**Табличные части:**
- list_tabular_parts, list_tabular_attributes
- find_objects_with_tabular, find_objects_by_attribute_in_tabular

**Формы:**
- list_forms, list_form_controls, list_form_events
- list_form_event_handlers, list_form_attributes_of_form
- list_form_commands, list_form_bindings
- find_controls_bound_to, get_default_forms

**Команды/Макеты:**
- list_commands, list_layouts, global варианты

**Модули/Рутины:**
- list_modules_of_owner, list_module_routines, list_common_module_routines
- list_exported_routines, get_routine_body
- find_routines_by_name, find_routines_by_signature, find_unused_routines

**Граф вызовов:**
- list_callers_of_routine, list_callees_of_routine
- call_graph_subtree, find_calls_between_owners

**Роли/Права:**
- list_roles_with_access_to_target, list_access_targets_of_role
- get_access_of_role_to_target

**Перечисления/Предопределенные:**
- list_enum_values, list_predefined_of_object
- find_predefined_by_name_in_object
- find_predefined_by_flag, find_predefined_by_kind, find_predefined_by_account_type

**Подписки на события:**
- list_event_subscriptions, list_event_subscriptions_of_object
- list_event_subscription_handlers, get_event_subscription_sources

**HTTP-сервисы:**
- list_http_services, list_url_templates_of_service, list_url_methods_of_template

**Перекрестные ссылки:**
- find_usages_of_object, find_objects_using_object
- find_documents_making_movements_into_register, find_journals_by_graph

### Tool 2: search_code
- find_routines_by_description -- полнотекстовый поиск по doc_description
- get_routine_body -- тело процедуры/функции по ID

### Tool 3: search_metadata_by_description
- search_metadata_by_description -- полнотекстовый поиск объектов по Synonym, Comment, Help, name

---

## Этапы реализации

### Этап 0: Подготовка инфраструктуры
- [ ] docker-compose.yml с Memgraph вместо Neo4j
- [ ] Проверить совместимость Cypher-диалекта Memgraph (индексы, constraints, fulltext)
- [ ] Базовый Dockerfile для Python MCP-сервиса (FastMCP + neo4j driver)

### Этап 1: Парсеры данных (loader)
- [ ] **Парсер ОтчетПоКонфигурации.txt** -- основной источник метаданных
  - Дерево: Configuration -> Category -> Object -> (Attributes, Resources, Dimensions, TabularParts, EnumValues, Commands, Layouts)
  - Свойства: Имя, Синоним, Комментарий, Тип, ПринадлежностьОбъекта и др.
- [ ] **Парсер BSL** -- процедуры/функции из .bsl файлов
  - Извлечение: имя, тип (Процедура/Функция), директива, экспорт, параметры, тело, JSDoc-комментарии
  - Построение CALLS связей по вызовам между рутинами
- [ ] **Парсер XML форм** (Ext/Form.xml) -- элементы, реквизиты, события, привязки
- [ ] **Парсер Predefined.xml** -- предопределенные элементы
- [ ] **Парсер Roles/*/Ext/Rights.xml** -- права ролей
- [ ] **Парсер EventSubscriptions/*.xml** -- подписки на события
- [ ] **Парсер ConfigDumpInfo.xml** -- маппинг GUID -> путь

### Этап 2: Загрузка в Memgraph
- [ ] Создание индексов и constraints (адаптация под Memgraph)
  - Memgraph: `CREATE INDEX ON :Label(property)` вместо Neo4j range index
  - Memgraph: fulltext через модуль `text_search` (Elasticsearch-like) или встроенный CONTAINS/regex
- [ ] Batch-загрузка узлов (UNWIND + MERGE)
- [ ] Batch-загрузка связей
- [ ] Проектная изоляция (project_name на каждом узле)

### Этап 3: MCP-сервер (FastMCP)
- [ ] Каркас: FastMCP с SSE-транспортом на порту 6001
- [ ] Tool: search_metadata -- маршрутизация по `op`, Cypher-запросы
- [ ] Tool: search_code -- fulltext по описаниям рутин
- [ ] Tool: search_metadata_by_description -- fulltext по описаниям объектов
- [ ] Конфигурация через env-переменные (PROJECT_NAME, NEO4J_URI, NEO4J_PASSWORD и др.)

### Этап 4: Docker и интеграция
- [ ] Dockerfile (python:3.12-slim, ~50MB base)
- [ ] docker-compose.yml (Memgraph + 5 MCP-сервисов)
- [ ] Тестирование на do_ame (минимальный проект, 361 объект)
- [ ] Тестирование на do_main (8086 объектов)

### Этап 5: Оптимизация
- [ ] Пул подключений к Memgraph
- [ ] Кэширование частых запросов
- [ ] Поэтапная загрузка (метаданные -> BSL -> формы -> роли)

---

## Особенности Memgraph vs Neo4j

| Функция | Neo4j | Memgraph | Решение |
|---------|-------|----------|---------|
| Bolt protocol | да | да | совместимо |
| Cypher | полный | ~95% | тестировать запросы |
| Fulltext index | CREATE FULLTEXT INDEX | text_search модуль или CONTAINS | адаптировать |
| UNWIND + MERGE | да | да | совместимо |
| Unique constraint | CREATE CONSTRAINT ... UNIQUE | CREATE CONSTRAINT ... UNIQUE | совместимо |
| JVM | требуется | нет (C++) | основной выигрыш |
| RAM (300K nodes) | ~1.2 GB | ~200-400 MB | 3-5x экономия |
| Startup time | 30-45 сек | 2-5 сек | 10x быстрее |

### Fulltext search в Memgraph

Memgraph не имеет встроенных fulltext-индексов как Neo4j. Варианты:
1. **CONTAINS / regex в WHERE** -- простой, но медленнее на больших данных
2. **text_search модуль (MAGE)** -- нужен Memgraph Platform с Elasticsearch
3. **Внешний поиск в Python** -- быстрый fulltext через whoosh/tantivy в памяти MCP-сервиса

Рекомендация: для ~300K узлов вариант 1 (CONTAINS) достаточно быстр. Если будет тормозить -- добавить Python-based fulltext (вариант 3).

---

## Структура проекта

```
1c-metacode-mcp-lite/
  docker-compose.yml
  .env
  mcp-service/
    Dockerfile
    requirements.txt
    app/
      __init__.py
      main.py              # Точка входа, FastMCP server
      config.py             # Env-конфигурация
      db/
        connection.py       # Memgraph/Neo4j подключение
        indexes.py          # Создание индексов
        loader.py           # Оркестратор загрузки
      parsers/
        metadata_report.py  # Парсер ОтчетПоКонфигурации.txt
        bsl_parser.py       # Парсер .bsl файлов
        form_parser.py      # Парсер Form.xml
        predefined_parser.py# Парсер Predefined.xml
        roles_parser.py     # Парсер Rights.xml
        events_parser.py    # Парсер EventSubscriptions
        config_dump.py      # Парсер ConfigDumpInfo.xml
      tools/
        search_metadata.py  # 97 операций
        search_code.py      # BSL-поиск
        search_description.py # Fulltext по описаниям
      cypher/
        queries.py          # Все Cypher-запросы
```

---

## Риски

1. **Совместимость Cypher** -- некоторые запросы могут потребовать адаптации для Memgraph
2. **Fulltext search** -- без нативных fulltext-индексов может быть медленнее на больших конфигурациях (ERP ~34K объектов)
3. **Парсер ОтчетПоКонфигурации.txt** -- формат проприетарный, нужно точно воспроизвести парсинг (кодировка, вложенность, ключ-значение)
4. **BSL-парсер** -- нужно корректно обрабатывать регионы, директивы, JSDoc, вложенные вызовы
5. **Время разработки** -- полная реализация 97 операций + 7 парсеров = значительный объем работы

## Приоритетный порядок

Минимально рабочий прототип (do_ame, 361 объект):
1. Memgraph + docker-compose
2. Парсер ОтчетПоКонфигурации.txt + загрузка в граф
3. Парсер BSL + загрузка рутин
4. MCP tool search_metadata (10-15 основных операций)
5. MCP tool search_code (get_routine_body, find_routines_by_name)

Затем наращивать: формы, роли, подписки, предопределенные, остальные операции.
