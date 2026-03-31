# 1c-metacode-mcp

## Среда

- Windows 10 Pro, Docker Desktop (WSL2 backend)
- Проект расположен на сетевом ресурсе `\\ame-srv-00001\ame_co\sukhov_ae\1c-workflow\docker-compose-mcp\1c-metacode-mcp`
- Локальный симлинк: `C:\sukhov_ae\ame_co` -> `\\ame-srv-00001\ame_co`

## Архитектура

- **Neo4j** (neo4j:5.26) — графовая БД, общая для всех MCP-сервисов
- **5 MCP-сервисов** (roctup/1c-mcp-metacode) — erp_main(:6001), erp_ame_do(:6002), ssl3(:6003), do_main(:6004), do_ame(:6005)
- Транспорт: SSE (`MCP_USE_SSE=true`), эндпоинты вида `http://localhost:600X/sse`
- Каждый MCP-сервис при старте загружает metadata в Neo4j

## Критические ограничения Docker Desktop на Windows

### UNC-пути и симлинки НЕ работают для volume mount

Docker Desktop (WSL2) не может монтировать:
- UNC-пути (`\\server\share\...`)
- Симлинки, ведущие на UNC-пути (`C:\sukhov_ae\ame_co` -> `\\ame-srv-00001\...`)
- Сетевые диски (`net use Z: \\...`)
- Относительные пути (`./data/...`) если docker-compose.yml лежит на сетевом ресурсе

**Решение:** data-папки должны быть на реальном локальном диске (например `C:\1c-metacode-data\`). Скопировать данные с сетевого ресурса на локальный диск и указать локальный путь в volumes.

### Neo4j JVM crash (assembler_x86.cpp ShouldNotReachHere)

neo4j:5.20 стабильно падает с JIT-крашем на этой машине. Обновление до **neo4j:5.26** решает проблему.

Не использовать `JAVA_TOOL_OPTIONS=-XX:+UseSerialGC` — конфликтует с GC Neo4j ("Multiple garbage collectors selected"). Параметр `-XX:TieredStopAtLevel=1` работает, но делает старт очень медленным.

### Neo4j зависает при docker stop

Если Neo4j упал с JVM-крашем, `docker stop` / `docker rm -f` часто зависают ("tried to kill container, but did not receive an exit event"). Единственный выход — перезапуск Docker Desktop. Если `wsl --shutdown` тоже висит:
```
taskkill /F /IM wslservice.exe
taskkill /F /IM wsl.exe
```
Затем запустить Docker Desktop заново.

## Процедура запуска (поэтапная)

Запускать все сервисы одновременно (`docker compose up -d`) опасно: 5 MCP-контейнеров одновременно нагружают Neo4j загрузкой метаданных, он может упасть.

### Шаг 1: Полная очистка (если были проблемы)
```bash
docker rm -f $(docker ps -aq)
docker volume rm 1c-metacode-mcp_1c_neo4j_data
```

### Шаг 2: Запустить Neo4j и дождаться healthy
```bash
cd <project-dir>
docker compose up -d 1c-neo4j
# Подождать ~45 секунд
docker ps  # Убедиться что статус: (healthy)
```

### Шаг 3: Запускать MCP-сервисы по одному
```bash
docker compose up -d 1c-metacode-erp_main
# Подождать ~30 секунд, проверить что Up и не Restarting
docker compose up -d 1c-metacode-erp_ame_do
# Подождать ~30 секунд
docker compose up -d 1c-metacode-ssl3
# и т.д.
```

### Шаг 4: Проверка SSE
```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:6001/sse --max-time 5
# Ожидаемый ответ: 200
```

## Конфигурация Neo4j (текущая рабочая)

- `image: neo4j:5.26` (не 5.20!)
- `heap_max_size: 1024m` (было 4096m — уменьшено для стабильности)
- healthcheck: `interval: 15s`, `timeout: 10s`, `retries: 10`, `start_period: 30s`

## Процедура остановки

Останавливать в обратном порядке — сначала MCP, потом Neo4j:
```bash
docker compose stop 1c-metacode-erp_main 1c-metacode-erp_ame_do 1c-metacode-ssl3 1c-metacode-do_main 1c-metacode-do_ame
# Подождать остановки
docker compose stop 1c-neo4j
docker compose down
```

Если Neo4j завис при остановке — не пытаться повторно, сразу рестарт Docker Desktop.
