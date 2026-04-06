<p align="center">
  <img src="https://img.shields.io/badge/1C:Предприятие-MCP_Сервер-yellow?style=for-the-badge&logo=1c&logoColor=white" alt="1C MCP" />
  <img src="https://img.shields.io/badge/Memgraph-Графовая_БД-6B4FBB?style=for-the-badge&logo=memgraph&logoColor=white" alt="Memgraph" />
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/ONNX-Semantic_Search-005CED?style=for-the-badge&logo=onnx&logoColor=white" alt="ONNX" />
  <img src="https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker" />
</p>

<h1 align="center">1C Metacode MCP Lite</h1>

<p align="center">
  <b>Легковесный MCP-сервер для графа метаданных 1С:Предприятие с семантическим поиском</b><br/>
  <sub>Memgraph + ONNX Embeddings + Hybrid Search &mdash; в 2.5 раза меньше RAM чем Neo4j + PyTorch</sub>
</p>

<p align="center">
  <a href="https://www.youtube.com/@svhovbase">
    <img src="https://img.shields.io/badge/YouTube-@svhovbase-FF0000?style=for-the-badge&logo=youtube&logoColor=white" alt="YouTube" />
  </a>
</p>

---

## Что это?

MCP-сервер, который загружает **метаданные конфигурации 1С:Предприятие** в **графовую базу данных** и предоставляет AI-ассистентам (Claude Code, Cursor, Windsurf и др.) два инструмента через [Model Context Protocol](https://modelcontextprotocol.io/):

1. **`search_metadata`** &mdash; структурный поиск по графу метаданных (14 операций)
2. **`search_by_embedding`** &mdash; семантический поиск по смыслу (E5 + cross-encoder + BM25)

AI-ассистент получает **полное структурное и семантическое знание** о конфигурации 1С:

- Все объекты метаданных (справочники, документы, регистры, перечисления и т.д.)
- Реквизиты, ресурсы, измерения с типами
- Формы, элементы управления, события, привязки
- BSL-код: процедуры, функции, сигнатуры, граф вызовов
- Роли и права доступа
- Перекрёстные ссылки между объектами (USED_IN, DO_MOVEMENTS_IN)
- Предопределённые элементы
- **Семантический поиск**: "где формируются проводки по зарплате?" &rarr; находит нужные процедуры по смыслу

---

## Сравнение с аналогами

### vs Neo4j + sentence-transformers (PyTorch)

| Компонент | Neo4j + PyTorch | **Lite (Memgraph + ONNX)** | Экономия |
|:----------|:---------------:|:--------------------------:|:--------:|
| Графовая БД | 1200 &ndash; 1500 МБ (JVM) | **100 &ndash; 500 МБ** (C++) | 3 &ndash; 5x |
| Embedding runtime | ~600 МБ (torch) | **~60 МБ** (onnxruntime) | 10x |
| Модель в RAM | ~900 МБ (FP32) | **~400 МБ** (INT8) | 2x |
| Хранение векторов | В heap БД | **sqlite-vec (на диске)** | 0 МБ RAM |
| Docker image | ~3.5 ГБ | **~1.4 ГБ** | 2.5x |
| **2 проекта + БД** | **~12 ГБ** | **~5 ГБ** | **2.5x** |

### Скорость

| Этап | Neo4j + PyTorch | **Lite** |
|:-----|:---------------:|:--------:|
| БД готова | 30 &ndash; 45 сек | **2 &ndash; 5 сек** |
| Загрузка конфигурации (8000+ объектов) | 5 &ndash; 20 мин | **1 &ndash; 3 мин** |
| MCP-сервер принимает запросы | после загрузки | **мгновенно** |
| Embedding query | ~80 мс (FP32) | **~50 мс** (INT8) |

### Стабильность

| Проблема | Neo4j | **Lite** |
|:---------|:-----:|:--------:|
| JVM crash / OOM | Бывает | **Нет JVM** |
| `docker stop` зависает | Часто | **Никогда** |
| Transaction timeout | При 5 сервисах | **Нет** (C++) |
| Требуется `-Xint` (без JIT) | Да | **Не нужен** |

---

## Архитектура

```
                  Memgraph (bolt://7687)
                        |
          +-------------+-------------+
          |                           |
     do_main :6004              do_ame :6005
     (Python FastMCP, SSE транспорт)
          |                           |
   +------+------+            +------+------+
   | Graph Search |            | Graph Search |
   | (14 операций)|            | (14 операций)|
   +------+------+            +------+------+
   | Hybrid Search|            | Hybrid Search|
   | E5 + CE + BM25           | E5 + CE + BM25
   +------+------+            +------+------+
   | sqlite-vec   |            | sqlite-vec   |
   | embeddings.db|            | embeddings.db|
   +--------------+            +--------------+
```

**Компоненты:**
- **Memgraph** (C++) &mdash; графовая БД, Bolt-протокол, совместима с Neo4j-драйвером
- **MCP-сервисы** &mdash; Python 3.12 + FastMCP, SSE-транспорт
- **E5-base** (ONNX INT8) &mdash; multilingual embedding модель (768 dim)
- **Cross-encoder** (ONNX INT8) &mdash; reranker для точности порядка
- **sqlite-vec** + **FTS5** &mdash; векторный + полнотекстовый поиск на диске
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

> Экспорт отчёта: **Конфигуратор &rarr; Конфигурация &rarr; Отчёт по конфигурации** (все объекты, `.txt`)

### 2. Укажите путь к данным

Отредактируйте `lite/docker-compose.yml`:

```yaml
volumes:
  - C:\path\to\your\data:/app/data
```

### 3. Запуск

```bash
cd lite/

# Собрать образ
docker compose build

# Поднять Memgraph
docker compose up -d memgraph

# Поднять MCP-сервис (подождать healthy)
docker compose up -d 1c-metacode-do_main
```

MCP-сервер начинает принимать запросы мгновенно. Данные и embeddings загружаются в фоне.

### 4. Подключение к AI-ассистенту

**Claude Code:**
```bash
claude mcp add do_main --transport sse http://localhost:6004/sse
```

**Cursor / Windsurf:** SSE-эндпоинт `http://localhost:6004/sse`

---

## MCP-инструменты

### `search_metadata` &mdash; структурный поиск (14 операций + code search)

```json
{"op": "browse", "name": "Контрагент"}
{"op": "object_structure", "name": "ДокументыПредприятия"}
{"op": "get_children", "name": "РеализацияТоваров", "child_type": "Attribute"}
{"op": "get_routines", "name": "ОтправитьЗапрос"}
{"op": "get_references", "name": "Контрагенты", "direction": "incoming"}
{"op": "find_routines_by_description", "text": "штрихкод"}
```

| Операция | Описание | Параметры |
|----------|----------|-----------|
| `browse` | Категории, объекты, поиск | `category`, `name` |
| `object_structure` | Полная карточка объекта | `name` |
| `get_children` | Реквизиты, ресурсы, измерения, ТЧ, команды, макеты | `name`, `child_type`, `tabular` |
| `find_by_child` | Объекты по реквизиту/ТЧ | `tabular`, `attribute` |
| `get_form` | Формы, элементы, события, привязки | `name`, `form`, `detail`, `limit` |
| `get_routines` | Процедуры/функции | `name`, `module`, `signature`, `export`, `unused` |
| `get_routine_body` | Исходный код процедуры | `name` или `id`, `owner` |
| `get_call_graph` | Граф вызовов | `name`/`id`, `direction`, `from`/`to` |
| `get_predefined` | Предопределённые элементы | `name`, `item`, `is_folder` |
| `get_access` | Права ролей | `role`, `target` |
| `get_references` | USED_IN, DO_MOVEMENTS_IN | `name`, `direction` |
| `get_subscriptions` | Подписки на события | `name` |
| `get_http_service` | HTTP-сервисы, URL-шаблоны | `name`, `template` |
| `resolve` | Разрешить QN, GUID, prefix | `qn`, `guid`, `prefix` |
| `find_routines_by_description` | Поиск по описанию кода | `text`, `export` |

> Старые имена операций (57 шт.) сохранены как алиасы для обратной совместимости.

### `search_by_embedding` &mdash; семантический поиск (опционально)

Ищет по смыслу, когда не знаешь точное имя объекта.

```json
{"op": "search_routines", "text": "работа со штрихкодами и кодированием"}
{"op": "search_objects", "text": "справочник видов операций с документами"}
{"op": "search_all", "text": "архив документов"}
{"op": "search_metadata_by_description", "text": "документы предприятия"}
```

**Как работает:**
1. Запрос &rarr; E5-base embedding (768 dim)
2. sqlite-vec KNN + FTS5 BM25 (hybrid search, адаптивные веса)
3. Cross-encoder reranking (top-20 &rarr; top-7)
4. Category boosting + dynamic threshold

> Tip: описывайте ЧТО объект делает, а не техническое имя.
> "справочник видов операций" &mdash; хорошо, "АМЕ_ВидыОпераций" &mdash; плохо.

---

## Конфигурация

| Переменная | Описание | По умолчанию |
|------------|----------|:------------:|
| `PROJECT_NAME` | Уникальный идентификатор проекта | `default` |
| `MEMGRAPH_URI` | Bolt URI для Memgraph | `bolt://localhost:7687` |
| `MCP_PORT` | Порт MCP-сервера | `6001` |
| `FULL_METADATA_RELOAD` | Полная перезагрузка данных при старте | `false` |
| `LOAD_BSL_SIGNATURES` | Парсить .bsl файлы | `true` |
| `LOAD_FORMS_FROM_XML` | Парсить Form.xml | `true` |
| `LOAD_PREDEFINED_VALUES` | Парсить предопределённые | `true` |
| `LOAD_ROLE_RIGHTS` | Парсить права ролей | `true` |
| `ENABLE_EMBEDDING` | Включить семантический поиск | `false` |
| `EMBEDDING_MODEL_PATH` | Путь к ONNX модели embedding | `/app/models/e5-base` |
| `RERANKER_MODEL_PATH` | Путь к ONNX модели cross-encoder | `/app/models/cross-encoder` |

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
                    |-- Command, Layout
                    +-- Module
                          +-- Routine --CALLS--> Routine

MetadataObject --USED_IN--> MetadataObject        (из типов реквизитов + BSL-кода)
MetadataObject --DO_MOVEMENTS_IN--> MetadataObject (документы -> регистры)
MetadataObject --GRANTS_ACCESS_TO--> MetadataObject (роли)
```

**USED_IN** строится из двух источников:
1. Типы реквизитов (`СправочникСсылка.Контрагенты` в metadata)
2. BSL-код (`Справочники.Контрагенты.НайтиПоКоду(...)` в исходниках)

**DO_MOVEMENTS_IN** строится из паттернов `Движения.ИмяРегистра.Записать()` в модулях документов.

---

## Embedding pipeline

```
Загрузка (фоновый поток):
  BSL-код + метаданные --> граф в Memgraph
       |
  Для каждого объекта/процедуры:
       |-- CamelCase split: АМЕ_ВидыОпераций --> АМЕ Виды Операций
       |-- Compose text: category | name | synonym | attributes
       |-- E5-base ONNX: text --> vector (768 dim)
       |-- sqlite-vec: сохранить на диск (embeddings.db)
       +-- FTS5: сохранить text для BM25

Поиск (при запросе):
  "справочник видов операций"
       |-- E5-base: query --> vector ("query: " prefix)
       |-- sqlite-vec KNN: top-20 по cosine
       |-- FTS5 BM25: top-20 по словам
       |-- Hybrid merge: 85% embedding + 15% BM25 (адаптивно)
       |-- Cross-encoder rerank: top-20 --> top-7
       |-- Category boost + dynamic threshold
       +-- Результат: [{name, score, category, ...}]
```

**Инкрементальные обновления:** при рестарте с `FULL_METADATA_RELOAD=true` пересчитываются только изменённые записи (по MD5-хешу текста).

---

## Потребление RAM

| Конфигурация | Без embedding | С embedding |
|:-------------|:-------------:|:-----------:|
| Memgraph | 100 &ndash; 500 МБ | 100 &ndash; 500 МБ |
| MCP-сервис (малая конфигурация, ~300 объектов) | ~80 МБ | ~1.3 ГБ |
| MCP-сервис (большая конфигурация, ~8000 объектов) | ~150 МБ | ~2 &ndash; 3 ГБ |

> E5-base (~400 МБ) + cross-encoder (~200 МБ) + sqlite-vec (на диске) = основной вес embedding.
> Без embedding сервис остаётся легковесным (~80-150 МБ).

---

## Добавление новых проектов

Скопируйте блок сервиса в `lite/docker-compose.yml`:

```yaml
1c-metacode-ssl3:
  image: svhov/1c-metacode-lite
  build: ./mcp-service
  restart: unless-stopped
  ports:
    - "6003:6001"
  volumes:
    - /path/to/ssl3:/app/data
  environment:
    - PROJECT_NAME=ssl3
    - MEMGRAPH_URI=bolt://memgraph:7687
    - MCP_PORT=6001
    - FULL_METADATA_RELOAD=true
    - ENABLE_EMBEDDING=true
  depends_on:
    memgraph:
      condition: service_healthy
```

---

## Известные ограничения

- **Поиск по кириллице** &mdash; `toLower()` в Memgraph не работает с кириллицей; поиск использует множественные варианты регистра из Python
- **Embedding пиковый RAM** &mdash; при первой индексации большой конфигурации (~90K routines) потребление может достигать 7 ГБ; после загрузки падает до 2-3 ГБ
- **Embedding модели не в образе** &mdash; ONNX модели (E5-base, cross-encoder) скачиваются отдельно в `models/`; без них `ENABLE_EMBEDDING=true` упадёт при старте

---

## Участники

<table>
  <tr>
    <td align="center">
      <a href="https://github.com/svhov">
        <img src="https://github.com/svhov.png" width="80" style="border-radius:50%" alt="svhov"/><br/>
        <sub><b>Сухов Андрей</b></sub>
      </a><br/>
      <sub>Автор проекта</sub>
    </td>
    <td align="center">
      <a href="https://github.com/anthropics">
        <img src="https://github.com/anthropics.png" width="80" style="border-radius:50%" alt="Claude"/><br/>
        <sub><b>Claude (Anthropic)</b></sub>
      </a><br/>
      <sub>AI co-author</sub>
    </td>
    <td align="center">
      <a href="https://github.com/jlowin">
        <img src="https://github.com/jlowin.png" width="80" style="border-radius:50%" alt="jlowin"/><br/>
        <sub><b>Jeremiah Lowin</b></sub>
      </a><br/>
      <sub>Автор <a href="https://github.com/jlowin/fastmcp">FastMCP</a></sub>
    </td>
  </tr>
</table>

---

## Лицензия

MIT

---

## Поддержать проект

Если проект оказался полезен, можете поддержать автора

| Сбербанк | `2202 2054 0027 9540` |
|:---------|:----------------------|
| Получатель | Сухов Андрей Евгеньевич |

---

<p align="center">
  <a href="https://www.youtube.com/@svhovbase">
    <img src="https://img.shields.io/badge/YouTube-@svhovbase-FF0000?style=flat-square&logo=youtube&logoColor=white" alt="YouTube" />
  </a>
  &nbsp;&nbsp;
  Если проект полезен — поставьте звезду!
</p>
