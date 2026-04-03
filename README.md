<p align="center">
  <img src="https://img.shields.io/badge/1C:Предприятие-MCP_Сервер-yellow?style=for-the-badge&logo=1c&logoColor=white" alt="1C MCP" />
  <img src="https://img.shields.io/badge/Memgraph-Графовая_БД-6B4FBB?style=for-the-badge&logo=memgraph&logoColor=white" alt="Memgraph" />
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker" />
</p>

<h1 align="center">1C Metacode MCP Lite</h1>

<p align="center">
  <b>Легковесный MCP-сервер для графа метаданных 1С:Предприятие</b><br/>
  <sub>Memgraph + Python FastMCP &mdash; в 3 раза меньше RAM чем Neo4j</sub>
</p>

<p align="center">
  <a href="https://www.youtube.com/@svhovbase">
    <img src="https://img.shields.io/badge/YouTube-@svhovbase-FF0000?style=for-the-badge&logo=youtube&logoColor=white" alt="YouTube" />
  </a>
</p>

---

## Что это?

MCP-сервер, который загружает **метаданные конфигурации 1С:Предприятие** в **графовую базу данных** и предоставляет инструменты для AI-ассистентов (Claude Code, Cursor, Windsurf и др.) через [Model Context Protocol](https://modelcontextprotocol.io/).

Ваш AI-ассистент получает **полное структурное знание** о конфигурации 1С:

- Все объекты метаданных (справочники, документы, регистры, перечисления и т.д.)
- Реквизиты, ресурсы, измерения с типами
- Формы, элементы управления, события, привязки
- BSL-код: процедуры, функции, сигнатуры, граф вызовов
- Роли и права доступа
- Перекрёстные ссылки между объектами (USED_IN)
- Предопределённые элементы

---

## Почему Lite? Сравнение RAM с Neo4j

Оригинальный подход использует **Neo4j** (графовая БД на JVM). На типичных машинах это означает:

| Компонент | Стек Neo4j | Lite (Memgraph) | Экономия |
|:----------|:----------:|:---------------:|:--------:|
| Графовая БД | **1200 -- 1500 МБ** | **100 -- 500 МБ** | 3 -- 5x |
| 1 MCP-сервис | 200 -- 400 МБ | 80 -- 150 МБ | 2 -- 3x |
| **2 проекта + БД** | **~2500 МБ** | **~600 -- 1000 МБ** | **2.5x** |
| **5 проектов + БД** | **~3500 МБ** | **~1000 -- 1500 МБ** | **2.5x** |

### Скорость запуска

| Этап | Стек Neo4j | Lite |
|:-----|:----------:|:----:|
| БД готова | 30 -- 45 сек | **2 -- 5 сек** |
| Загрузка маленькой конфигурации (361 объект) | ~30 сек | **6 сек** |
| Загрузка большой конфигурации (8000+ объектов) | 5 -- 20 мин | **1 -- 3 мин** |
| MCP-сервер принимает запросы | после полной загрузки | **мгновенно** (загрузка в фоне) |

### Стабильность

| Проблема | Стек Neo4j | Lite |
|:---------|:----------:|:----:|
| JVM crash (`assembler_x86.cpp`) | Бывает на некоторых хостах | **Нет JVM вообще** |
| `docker stop` зависает после краша | Часто | **Никогда** |
| Transaction timeout при нагрузке | При загрузке 5 сервисов | **Нет** (нативный C++) |
| Требуется `-Xint` (без JIT) | Да, замедляет всё | **Не нужен** |
| Строгий порядок запуска обязателен | Да, поэтапно | **Нет, можно все сразу** |

---

## Архитектура

```
                  Memgraph (bolt://7687)
                        |
       +--------+-------+-------+--------+
       |        |       |       |        |
   erp_main  erp_ame  ssl3  do_main  do_ame
    :6001     :6002   :6003  :6004   :6005
       (Python FastMCP, SSE транспорт)
```

- **Memgraph** -- графовая БД (Bolt-протокол, Cypher-запросы, совместима с Neo4j-драйвером)
- **MCP-сервисы** -- Python 3.12 + FastMCP, SSE-транспорт
- Все проекты используют один Memgraph, изолированы по `project_name`

---

## Быстрый старт

### 1. Подготовьте данные

Каждому проекту нужна директория с:

```
ваш-проект/
  metadata/
    ОтчетПоКонфигурации.txt     # Отчет по конфигурации (UTF-16 или UTF-8)
  code/
    ConfigDumpInfo.xml            # GUID-маппинг (опционально)
    ОбщиеМодули/                  # BSL исходники
    Справочники/
    Документы/
    ...
```

> Экспорт отчёта по конфигурации из Конфигуратора 1С:
> **Конфигурация -> Отчёт по конфигурации** (выбрать все объекты, сохранить как `.txt`)

### 2. Укажите путь к данным

Отредактируйте `lite/docker-compose.yml` -- замените `/path/to/erp_main` на реальный путь:

```yaml
volumes:
  - /path/to/erp_main:/app/data
```

> **Для Windows**: используйте локальные пути типа `C:\1c-data\erp_main`, НЕ UNC-пути.

### 3. Запуск

```bash
cd lite/
docker compose up -d
```

Всё. Memgraph стартует за ~3 секунды, MCP-сервер начинает принимать запросы мгновенно, данные загружаются в фоне.

### 4. Проверка

```bash
# Проверить что контейнеры запущены
docker ps

# Тест SSE-эндпоинта (ожидаемый ответ: 200)
curl -s -o /dev/null -w "%{http_code}" http://localhost:6001/sse --max-time 5
```

---

## Подключение к AI-ассистенту

### Claude Code

```bash
claude mcp add erp_main --transport sse http://localhost:6001/sse
```

Или в `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "erp_main": { "type": "sse", "url": "http://localhost:6001/sse" }
  }
}
```

### Cursor / Windsurf / другие MCP-клиенты

SSE-эндпоинт: `http://localhost:6001/sse`

---

## MCP-инструменты

### `search_metadata` -- 57 операций

Основной инструмент. Принимает JSON с полем `"op"`:

```json
{"op": "list_categories"}
{"op": "list_objects_by_name", "name": "Контрагенты"}
{"op": "object_structure", "name": "ДокументыПредприятия"}
{"op": "list_attributes_with_type", "name": "АМЕ_ДоговорыКонтрагентов"}
{"op": "find_routines_by_name", "name": "ОтправитьЗапрос"}
{"op": "get_routine_body", "id": "do_ame/АМЕ/CommonModules/КоннекторHTTP.ОтправитьЗапрос"}
{"op": "find_usages_of_object", "name": "Контрагенты"}
```

<details>
<summary><b>Полный список операций (нажмите чтобы развернуть)</b></summary>

**Объекты и категории**
| Операция | Описание |
|----------|----------|
| `list_categories` | Список всех категорий метаданных |
| `list_objects_by_category` | Объекты в категории |
| `list_objects_by_name` | Поиск объектов по имени (CONTAINS) |
| `object_structure` | Полная карточка объекта: реквизиты, формы, ссылки |
| `resolve_qn` | Разрешить квалифицированное имя в узел |
| `resolve_qn_prefix` | Поиск узлов по префиксу QN |
| `find_by_guid` | Поиск узла по GUID |
| `get_node_properties` | Все свойства узла |

**Реквизиты и структура**
| Операция | Описание |
|----------|----------|
| `list_attributes` / `list_attributes_with_type` | Реквизиты объекта с типами |
| `list_resources` / `list_resources_with_type` | Ресурсы регистра |
| `list_dimensions` / `list_dimensions_with_type` | Измерения регистра |
| `list_characteristics` / `list_characteristics_with_type` | Характеристики |
| `list_tabular_parts` | Табличные части |
| `list_tabular_attributes` | Реквизиты табличной части |
| `find_objects_with_tabular` | Объекты с определённой табличной частью |
| `find_objects_by_attribute_in_tabular` | Поиск по реквизиту в табличной части |

**Формы**
| Операция | Описание |
|----------|----------|
| `list_forms` | Формы объекта |
| `list_form_controls` | Элементы формы |
| `list_form_events` / `list_form_event_handlers` | События формы |
| `list_form_attributes_of_form` | Реквизиты формы |
| `list_form_commands` | Команды формы (кнопки) |
| `list_form_bindings` | Привязки элементов к реквизитам |
| `find_controls_bound_to` | Элементы привязанные к реквизиту |
| `get_default_forms` | Основные формы |

**Команды и макеты**
| Операция | Описание |
|----------|----------|
| `list_commands` | Команды объекта |
| `list_layouts` | Макеты объекта |

**Модули и код**
| Операция | Описание |
|----------|----------|
| `list_modules_of_owner` | Модули объекта |
| `list_module_routines` | Процедуры/функции модуля |
| `list_common_module_routines` | Процедуры общего модуля |
| `list_exported_routines` | Экспортные процедуры |
| `get_routine_body` | Полный исходный код процедуры |
| `find_routines_by_name` | Поиск процедур по имени |
| `find_routines_by_signature` | Поиск по тексту сигнатуры |
| `find_unused_routines` | Неиспользуемые процедуры |

**Граф вызовов**
| Операция | Описание |
|----------|----------|
| `list_callers_of_routine` | Кто вызывает процедуру |
| `list_callees_of_routine` | Что вызывает процедура |
| `call_graph_subtree` | Дерево вызовов (глубина 1-3) |
| `find_calls_between_owners` | Вызовы между двумя модулями/объектами |

**Перечисления и предопределённые**
| Операция | Описание |
|----------|----------|
| `list_enum_values` | Значения перечисления |
| `list_predefined_of_object` | Предопределённые элементы |
| `find_predefined_by_name_in_object` | Поиск предопределённого по имени |
| `find_predefined_by_flag` | Поиск по признаку папка/элемент |

**Роли и доступ**
| Операция | Описание |
|----------|----------|
| `list_roles_with_access_to_target` | Роли с доступом к объекту |
| `list_access_targets_of_role` | Объекты доступные роли |
| `get_access_of_role_to_target` | Конкретные права роли на объект |

**Перекрёстные ссылки**
| Операция | Описание |
|----------|----------|
| `find_usages_of_object` | Кто ссылается на объект (через типы) |
| `find_objects_using_object` | На что ссылается объект |
| `find_documents_making_movements_into_register` | Документы делающие движения в регистр |
| `find_journals_by_graph` | Графы журналов |

**События и HTTP**
| Операция | Описание |
|----------|----------|
| `list_event_subscriptions` | Все подписки на события |
| `list_event_subscriptions_of_object` | Подписки для объекта |
| `list_http_services` | HTTP-сервисы |
| `list_url_templates_of_service` | URL-шаблоны |
| `list_url_methods_of_template` | HTTP-методы |

</details>

### `search_code` -- поиск по BSL-коду

```json
{"op": "find_routines_by_description", "text": "HTTP", "export": true}
{"op": "get_routine_body", "name": "ВыполнитьЗапрос"}
```

### `search_metadata_by_description` -- полнотекстовый поиск

```json
{"text": "премия"}
{"text": "контрагент", "category": "Справочники"}
```

---

## Добавление новых проектов

Скопируйте блок сервиса в `lite/docker-compose.yml`:

```yaml
1c-metacode-ssl3:
  image: svhov/1c-metacode-lite
  build: ./mcp-service
  restart: unless-stopped
  ports:
    - "6003:6001"                         # уникальный порт на хосте
  volumes:
    - /path/to/ssl3:/app/data             # ваши данные
  environment:
    - PROJECT_NAME=ssl3                   # уникальное имя
    - MEMGRAPH_URI=bolt://memgraph:7687
    - MCP_PORT=6001
    - FULL_METADATA_RELOAD=true
  depends_on:
    memgraph:
      condition: service_healthy
```

## Конфигурация

| Переменная | Описание | По умолчанию |
|------------|----------|:------------:|
| `PROJECT_NAME` | Уникальный идентификатор проекта | `default` |
| `MEMGRAPH_URI` | Bolt URI для Memgraph | `bolt://localhost:7687` |
| `MCP_PORT` | Порт MCP-сервера внутри контейнера | `6001` |
| `FULL_METADATA_RELOAD` | Очистить и перезагрузить все данные при старте | `false` |
| `LOAD_BSL_SIGNATURES` | Парсить .bsl файлы (процедуры/функции) | `true` |
| `LOAD_FORMS_FROM_XML` | Парсить файлы Form.xml | `true` |
| `LOAD_PREDEFINED_VALUES` | Парсить предопределённые элементы | `true` |
| `LOAD_ROLE_RIGHTS` | Парсить права ролей | `true` |

---

## Модель графа

```
Project
  +-- Configuration
        +-- MetadataCategory
              +-- MetadataObject
                    |-- Attribute (с type_info)
                    |-- Resource
                    |-- Dimension
                    |-- TabularPart
                    |     +-- Attribute
                    |-- Form
                    |     |-- FormControl --BINDS_TO--> FormAttribute
                    |     +-- FormEvent
                    |-- EnumValue
                    |-- PredefinedItem
                    |-- Command
                    |-- Layout
                    +-- Module
                          +-- Routine --CALLS--> Routine

MetadataObject --USED_IN--> MetadataObject
MetadataObject --GRANTS_ACCESS_TO--> MetadataObject (роли)
```

---

## Известные ограничения

- **Нет векторного/embedding поиска** -- только шаблонные запросы, LLM не нужен
- **Нет запросов на естественном языке** -- только JSON-операции (by design: ноль внешних зависимостей)
- **Поиск по кириллице** -- `toLower()` в Memgraph не работает с кириллицей; поиск использует множественные варианты регистра из Python
- **USED_IN связи** строятся только из типов реквизитов (`СправочникСсылка.X`), не из всех возможных ссылок

---

## Лицензия

MIT

---

<p align="center">
  <a href="https://www.youtube.com/@svhovbase">
    <img src="https://img.shields.io/badge/YouTube-@svhovbase-FF0000?style=flat-square&logo=youtube&logoColor=white" alt="YouTube" />
  </a>
</p>
