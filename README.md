<p align="center">
  <img src="https://img.shields.io/badge/1C:Предприятие-MCP_Сервер-yellow?style=for-the-badge&logo=1c&logoColor=white" alt="1C MCP" />
  <img src="https://img.shields.io/badge/Memgraph-Графовая_БД-6B4FBB?style=for-the-badge&logo=memgraph&logoColor=white" alt="Memgraph" />
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/ONNX-Semantic_Search-005CED?style=for-the-badge&logo=onnx&logoColor=white" alt="ONNX" />
  <img src="https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker" />
</p>

<h1 align="center">1C Metacode MCP Lite</h1>

<p align="center">
  <b>Превращает любую конфигурацию 1С в граф, который понимает AI</b><br/>
  <sub>Memgraph + ONNX E5 + sqlite-vec — индексирует ERP с 638&nbsp;000 рутин в одном Docker-контейнере на 2.5 ГБ RAM</sub>
</p>

<p align="center">
  <a href="https://www.youtube.com/@svhovvv">
    <img src="https://img.shields.io/badge/YouTube-Подписаться_на_канал-FF0000?style=for-the-badge&logo=youtube&logoColor=white" alt="YouTube @svhovvv" />
  </a>
</p>

---

## Если коротко

Ваш AI-ассистент (Claude Code, Cursor, Windsurf, Cline — любой с поддержкой MCP) знает каждый справочник, документ, регистр, реквизит, форму, роль, процедуру и связь между ними **в вашей конфигурации 1С**. Без LLM, без облака, без отправки кода наружу. Локально, в одном `docker compose up`.

```text
Вы:    "Где формируются проводки по зарплате?"
Claude: → search_by_embedding "формирование проводок по зарплате"
        → НачислениеЗарплаты.МодульМенеджера.СформироватьДвижения()
        → score 0.87
        Открываю модуль...
```

И всё это работает на конфигурациях, где **638&nbsp;000 BSL-рутин**, **21&nbsp;000 объектов метаданных** и **20+ ГБ исходников** — без OOM, без зависаний, с возможностью продолжить индексацию после рестарта.

---

## Зачем это нужно

1С — это миры на сотни тысяч строк BSL, тысячи объектов метаданных, перекрёстных ссылок и форм. AI-ассистенты в этом мире **слепы**: они не понимают, какой реквизит у какого справочника, кто кого вызывает и где формируются движения. Каждый запрос «найди где обрабатывается такой-то документ» превращается в час чтения логов и ручных Cypher-запросов.

**1C Metacode MCP Lite** решает это так:

- **Парсит** отчёт по конфигурации, BSL-исходники, формы, роли, GUID-маппинг — за минуты
- **Складывает** всё в граф Memgraph (объекты, реквизиты, вызовы, ссылки `USED_IN` и `DO_MOVEMENTS_IN`)
- **Индексирует** семантически через E5-base + cross-encoder ONNX-модели локально, без API
- **Отдаёт** ассистенту через MCP-протокол два инструмента: структурный (граф) и семантический (embeddings)

Никаких токенов в облако. Никаких подписок. Один контейнер — один проект.

---

## Что отличает Lite от обычных решений

### vs Neo4j + sentence-transformers (PyTorch)

| Компонент | Neo4j + PyTorch | **Lite (Memgraph + ONNX)** | Экономия |
|:----------|:---------------:|:--------------------------:|:--------:|
| Графовая БД | 1200 — 1500 МБ (JVM) | **100 — 500 МБ** (C++) | 3 — 5x |
| Embedding runtime | ~600 МБ (torch) | **~60 МБ** (onnxruntime) | 10x |
| Модель в RAM | ~900 МБ (FP32) | **~400 МБ** (FP16) | 2x |
| Хранение векторов | В heap БД | **sqlite-vec (на диске)** | 0 МБ RAM |
| Docker image | ~3.5 ГБ | **~1.4 ГБ** | 2.5x |
| **2 проекта + БД** | **~12 ГБ** | **~5 ГБ** | **2.5x** |

### Скорость

| Этап | Neo4j + PyTorch | **Lite** |
|:-----|:---------------:|:--------:|
| БД готова к запросам | 30 — 45 сек | **2 — 5 сек** |
| Загрузка средней конфигурации (~8000 объектов) | 5 — 20 мин | **1 — 3 мин** |
| MCP-сервер принимает запросы | после загрузки | **сразу** (load в фоне) |
| Embedding query | ~80 мс | **~50 мс** |

### Стабильность

| Проблема | Neo4j + PyTorch | **Lite** |
|:---------|:---------------:|:--------:|
| JVM crash / OOM | бывает | нет JVM |
| `docker stop` зависает | часто | никогда |
| Transaction timeout на 5 сервисах | да | нет (C++) |
| OOM на больших базах при индексации | гарантированно | **streaming + resume** |

---

## Демо за 60 секунд

```bash
git clone https://github.com/svhov/1c-metacode-mcp.git
cd 1c-metacode-mcp/lite

# Скопировать шаблон compose и подставить свои пути
cp docker-compose.example.yml docker-compose.yml
$EDITOR docker-compose.yml

# Запустить
docker compose build
docker compose up -d memgraph
docker compose up -d 1c-metacode-do_main

# Подключить к Claude Code
claude mcp add do_main --transport sse http://localhost:6004/sse
```

Через 30 секунд после запуска MCP-сервер **уже отвечает на запросы**, пока в фоне грузятся данные. Через несколько минут доступен полнотекстовый и семантический поиск.

```text
> /mcp do_main browse Контрагент
{
  "found": [
    {"name": "Контрагенты", "category": "Справочники", "qn": "do_main/Конфигурация/Справочники/Контрагенты"},
    {"name": "КонтрагентыКонтактныеЛица", ...}
  ]
}

> /mcp do_main search_by_embedding "справочник для хранения банковских счетов"
{
  "results": [
    {"name": "БанковскиеСчета", "category": "Справочники", "score": 0.91},
    {"name": "СчетаОрганизаций", "category": "Справочники", "score": 0.74}
  ]
}
```

Ваш AI-ассистент теперь **знает каждый угол** вашей конфигурации.

---

## Что нового

### 2026-04 — Streaming embedding pipeline

Поддержка **гигантских конфигураций**, для которых раньше падал OOM на этапе индексации.

- **Bounded RAM** — старый код накапливал все векторы в Python-list, потолок ~10 ГБ для ERP. Новый pipeline embed → flush → free чанками по 2000 рутин: peak RAM **~2.5 ГБ** независимо от размера базы.
- **Resumable** — каждый чанк сразу коммитится в `embeddings.db`. Контейнер упал на 50%? При следующем старте `existing_hashes` подхватит уже сделанное и продолжит ровно с места обрыва. Никаких «всё или ничего».
- **Прогресс в логах** — раз в чанк строка вида `Embedded routines: 8000/637877 (skipped=6000, chunk=2000, inner_batch=64)`. Видно ETA и происходящее в реальном времени.
- **Адаптивный inner batch_size** — если в чанке текстов оказались длинные рутины (>800 / >2000 символов средняя длина), внутренний batch_size автоматически уменьшается с 64 до 32 / 16, чтобы не выжирать память на padding.
- **Bulk-commit** — `SqliteVecStore.add_many()` пишет весь чанк за одну транзакцию вместо одного `commit()` на запись. Это не только спасло память, но и **ускорило** запись в десятки раз.

### 2026-04 — Обогащённый текст для search_objects

Раньше объекты индексировались только по имени: `"Контрагенты"`. Запросы вроде «справочник для хранения контрагентов» давали `score 0.15` или вообще не находили. Теперь embedding-текст собирается из контекста графа:

```text
Справочник Контрагенты — хранение данных о контрагентах организации.
Реквизиты: ИНН, КПП, НаименованиеПолное, ЮрФизЛицо, ГруппаДоступа, ОсновнойБанковскийСчет.
Табличные части: КонтактнаяИнформация, ДополнительныеРеквизиты.
Используется в: ДокументыПредприятия, Корреспонденция, ШаблоныДокументов
```

- Русские названия категорий (`Справочник` вместо `Справочники`, `Документ` вместо `Документы`)
- До 10 реквизитов и до 7 входящих ссылок
- Табличные части и владелец (для подчинённых справочников)
- Шумовые категории (`ОбщиеКартинки`, `ЭлементыСтиля`, `Стили`) исключены

Точность `search_objects` на тестовой `do_ame` поднялась с 4/10 до 10/10.

### 2026-04 — Heartbeat, который не мешает читать логи

Две независимые «частоты»:

- **`SSE_PING_INTERVAL=10s`** — keep-alive пинг прямо в TCP-сокет SSE-соединения через `EventSourceResponse(ping=...)`. Клиенты не видят, прокси и корпоративные firewall'ы не закрывают idle-сессии.
- **`HEARTBEAT_LOG_INTERVAL=30s`** — строка в `docker logs` формата `heartbeat — project=do_main active_sse_sessions=2`. **Включается ТОЛЬКО после завершения индексации**, чтобы не зашумлять прогресс embedding'а длинных конфигураций.

Счётчик активных SSE-сессий ведётся через monkey-patch `mcp.server.sse.SseServerTransport.connect_sse`.

### 2026-04 — Прочие улучшения

- **Batched GUID writes** — раньше `_load_guid_map()` делал один `MATCH/SET` на каждый GUID (для ERP это 200k+ запросов и ~4 часа). Теперь пачками по 500 в одном `UNWIND` — минуты вместо часов.
- **bid (Бюджетирование и документооборот)** добавлен как пример проекта в шаблон compose.
- **`lite/docker-compose.example.yml`** — отдельный шаблон с placeholder-путями и подробными комментариями. Реальный `lite/docker-compose.yml` теперь в `.gitignore`.

---

## Архитектура

```
                  Memgraph (bolt://7687)
                        |
          +-------------+-------------+
          |                           |
     do_main :6004              erp_main :6001
     (Python FastMCP, SSE)      (Python FastMCP, SSE)
          |                           |
   +------+------+            +------+------+
   | Graph Search |            | Graph Search |
   | (14 операций)|            | (14 операций)|
   +------+------+            +------+------+
   | Hybrid Search|            | Hybrid Search|
   | E5 + CE + BM25            | E5 + CE + BM25
   +------+------+            +------+------+
   | sqlite-vec   |            | sqlite-vec   |
   | embeddings.db|            | embeddings.db|
   +--------------+            +--------------+
```

**Компоненты:**

- **Memgraph** (C++) — графовая БД, Bolt-протокол, совместима с Neo4j-драйвером
- **MCP-сервисы** — Python 3.12 + FastMCP 3.2, SSE-транспорт
- **E5-base** (multilingual) — embedding модель 768 dim, prefix `query:` / `passage:`
- **Cross-encoder** — ONNX reranker для точности порядка результатов
- **sqlite-vec** + **FTS5** — векторный + полнотекстовый поиск на диске
- Все проекты используют один Memgraph, изолируются по `project_name`

---

## Быстрый старт

### 1. Подготовьте данные

Каждому проекту нужна директория с двумя поддиректориями:

```
ваш-проект/
  metadata/
    ОтчетПоКонфигурации.txt     # Конфигуратор -> Конфигурация -> Отчёт по конфигурации
  code/
    ConfigDumpInfo.xml            # GUID-маппинг (опционально)
    ОбщиеМодули/                  # BSL исходники
    Справочники/
    Документы/
    ...
```

> Экспорт отчёта: **Конфигуратор → Конфигурация → Отчёт по конфигурации** (формат `.txt`, все объекты).
> Экспорт исходников: **Конфигурация → Выгрузить конфигурацию в файлы**.

### 2. Создайте свой compose

```bash
cd lite/
cp docker-compose.example.yml docker-compose.yml
```

В новом `docker-compose.yml` подставьте свои пути:

```yaml
volumes:
  - /path/to/data/do_main:/app/data
```

И уникальное имя проекта:

```yaml
environment:
  - PROJECT_NAME=do_main
```

### 3. Запуск

```bash
docker compose build
docker compose up -d memgraph
docker compose up -d 1c-metacode-do_main
```

MCP-сервер **сразу** принимает запросы. Данные и embeddings грузятся в фоне — прогресс виден в `docker logs`.

### 4. Подключите к AI-ассистенту

**Claude Code:**

```bash
claude mcp add do_main --transport sse http://localhost:6004/sse
```

**Cursor / Windsurf / Cline:** SSE-эндпоинт `http://localhost:6004/sse`.

---

## MCP-инструменты

### `search_metadata` — структурный поиск

14 операций над графом + code search. Все принимают JSON через единый tool-вход.

```json
{"op": "browse", "name": "Контрагент"}
{"op": "object_structure", "name": "ДокументыПредприятия"}
{"op": "get_children", "name": "РеализацияТоваров", "child_type": "Attribute"}
{"op": "get_routines", "name": "ОтправитьЗапрос"}
{"op": "get_references", "name": "Контрагенты", "direction": "incoming"}
{"op": "find_routines_by_description", "text": "штрихкод"}
```

| Операция | Описание |
|----------|----------|
| `browse` | Категории, объекты, поиск по имени |
| `object_structure` | Полная карточка объекта |
| `get_children` | Реквизиты, ресурсы, измерения, табличные части, команды, макеты |
| `find_by_child` | Найти объекты по имени реквизита/ТЧ |
| `get_form` | Формы, элементы управления, события, привязки |
| `get_routines` | Процедуры/функции по имени, модулю, флагам |
| `get_routine_body` | Исходный код процедуры |
| `get_call_graph` | Кто вызывает / кого вызывает / дерево вызовов |
| `get_predefined` | Предопределённые элементы справочников |
| `get_access` | Права ролей |
| `get_references` | USED_IN, DO_MOVEMENTS_IN |
| `get_subscriptions` | Подписки на события |
| `get_http_service` | HTTP-сервисы, URL-шаблоны |
| `resolve` | Разрешить qualified_name, GUID или префикс |
| `find_routines_by_description` | Поиск процедур по описанию (BSL doc-comments) |

### `search_by_embedding` — семантический поиск

Ищет по смыслу, когда не знаете точное имя.

```json
{"op": "search_routines", "text": "работа со штрихкодами и кодированием"}
{"op": "search_objects", "text": "справочник видов операций с документами"}
{"op": "search_all", "text": "архив документов"}
{"op": "search_metadata_by_description", "text": "документы предприятия"}
```

**Pipeline запроса:**

1. E5-base токенизация и embedding (768 dim, prefix `query:`)
2. sqlite-vec KNN top-20 по cosine + FTS5 BM25 top-20 по словам
3. Hybrid merge с адаптивными весами (85% embedding + 15% BM25, либо 50/50 если embedding слабый)
4. Cross-encoder reranking top-20 → top-7
5. Category boost + dynamic threshold

> **Tip:** описывайте ЧТО объект делает, а не его техническое имя.
> Хорошо: «справочник видов операций с документами».
> Плохо: «АМЕ_ВидыОпераций».

---

## Конфигурация

| Переменная | Описание | По умолчанию |
|------------|----------|:------------:|
| `PROJECT_NAME` | Уникальный идентификатор проекта | `default` |
| `MEMGRAPH_URI` | Bolt URI для Memgraph | `bolt://memgraph:7687` |
| `MCP_PORT` | Порт MCP-сервера в контейнере | `6001` |
| `FULL_METADATA_RELOAD` | Пересоздавать граф при старте | `false` |
| `LOAD_BSL_SIGNATURES` | Парсить .bsl файлы | `true` |
| `LOAD_FORMS_FROM_XML` | Парсить Form.xml | `true` |
| `LOAD_PREDEFINED_VALUES` | Парсить Predefined.xml | `true` |
| `LOAD_ROLE_RIGHTS` | Парсить Rights.xml | `true` |
| `ENABLE_EMBEDDING` | Включить семантический поиск | `false` |
| `EMBEDDING_MODEL_PATH` | Путь к ONNX модели embedding | `/app/models/e5-base` |
| `RERANKER_MODEL_PATH` | Путь к ONNX cross-encoder | `/app/models/cross-encoder` |
| `EMBEDDING_BATCH_SIZE` | Inner batch для ONNX-инференса | `64` |
| `EMBEDDING_STREAM_CHUNK` | Размер чанка streaming-индексации | `2000` |
| `EMBEDDING_QUERY_PREFIX` | E5-префикс для запросов | `query:` |
| `EMBEDDING_PASSAGE_PREFIX` | E5-префикс для документов | `passage:` |
| `SSE_PING_INTERVAL` | TCP keep-alive SSE-соединения, сек | `10` |
| `HEARTBEAT_LOG_INTERVAL` | Heartbeat в docker logs, сек | `30` |

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

MetadataObject --USED_IN--> MetadataObject        (типы реквизитов + BSL-код)
MetadataObject --DO_MOVEMENTS_IN--> MetadataObject (документы -> регистры)
MetadataObject --GRANTS_ACCESS_TO--> MetadataObject (роли)
```

**`USED_IN`** строится из двух источников:

1. Типы реквизитов (`СправочникСсылка.Контрагенты` в metadata)
2. BSL-код (`Справочники.Контрагенты.НайтиПоКоду(...)` в исходниках)

**`DO_MOVEMENTS_IN`** строится из паттернов `Движения.ИмяРегистра.Записать()` в модулях документов.

---

## Известные ограничения

- **Кириллица в `toLower()`** — Memgraph не работает с кириллицей в `toLower()`; поиск использует множественные варианты регистра из Python.
- **Embedding пиковый RAM** — для гигантских баз (~600k+ рутин) индексация теперь идёт **streaming-режимом** с peak ~2.5 ГБ. Для средних баз (~8k объектов) ~1 ГБ.
- **Embedding модели не в образе** — ONNX-модели (E5-base, cross-encoder) скачиваются отдельно в `lite/mcp-service/models/`. Без них `ENABLE_EMBEDDING=true` упадёт при старте.
- **Скорость embedding'а** — на CPU (без GPU) 600k рутин индексируются ~10–18 часов. Streaming делает это безопасным (resumable, без OOM), но не быстрым. Для регулярных пересборок имеет смысл переходить на GPU или e5-small.

---

## YouTube — про 1С, AI и автоматизацию

<p align="center">
  <a href="https://www.youtube.com/@svhovvv">
    <img src="https://img.shields.io/badge/YouTube-@svhovvv-FF0000?style=for-the-badge&logo=youtube&logoColor=white" alt="YouTube" />
  </a>
</p>

На канале **[@svhovvv](https://www.youtube.com/@svhovvv)** — практика построения AI-инструментов вокруг 1С:Предприятие, разборы реальных конфигураций, MCP-серверы, Claude Code, автоматизация рутины разработчика, и закулисье этого проекта. Если зашло — **подписывайтесь**, это лучшая благодарность автору.

---

## Поддержать проект

Если 1C Metacode MCP Lite сэкономил вам часы разбирательства в чужих конфигурациях — можно поблагодарить рублём.

| Сбербанк | `2202 2054 0027 9540` |
|:---------|:----------------------|
| Получатель | Сухов Андрей Евгеньевич |

И не забудьте про **[звезду на GitHub](https://github.com/svhov/1c-metacode-mcp)** и **[подписку на YouTube](https://www.youtube.com/@svhovvv)** — это бесплатно и сильно помогает проекту находить аудиторию.

---

## Авторы

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

[MIT](LICENSE) — Copyright (c) 2026 Сухов Андрей Евгеньевич.

Используйте, форкайте, дописывайте, продавайте — без ограничений. Только не убирайте упоминание автора в коде.

---

<p align="center">
  <a href="https://www.youtube.com/@svhovvv">
    <img src="https://img.shields.io/badge/YouTube-@svhovvv-FF0000?style=flat-square&logo=youtube&logoColor=white" alt="YouTube" />
  </a>
  &nbsp;&nbsp;
  <a href="https://github.com/svhov/1c-metacode-mcp">
    <img src="https://img.shields.io/badge/GitHub-Поставить_★-181717?style=flat-square&logo=github&logoColor=white" alt="GitHub" />
  </a>
</p>

<p align="center">
  <sub>Сделано в России для разработчиков 1С — теми, кто устал искать «где же это объявлено» руками.</sub>
</p>
