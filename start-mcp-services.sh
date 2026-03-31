#!/bin/bash
# Поэтапный запуск MCP-сервисов с интервалами
# Запуск: bash start-mcp-services.sh

CD="//ame-srv-00001/ame_co/sukhov_ae/1c-workflow/docker-compose-mcp/1c-metacode-mcp"
LOG="$CD/start-mcp.log"

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S') | $1" | tee -a "$LOG"
}

start_service() {
  local name=$1
  local port=$2
  log ">>> Запуск $name ..."
  cd "$CD" && docker compose up -d "$name" >> "$LOG" 2>&1
  if [ $? -eq 0 ]; then
    log "OK: $name запущен"
  else
    log "ОШИБКА: $name не удалось запустить"
  fi
  # Проверка через 30 секунд
  sleep 30
  local status=$(docker ps --filter "name=$name" --format "{{.Status}}" 2>/dev/null)
  log "Статус $name: $status"
  if [ -n "$port" ]; then
    local code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$port/mcp" --max-time 5 2>/dev/null)
    log "HTTP /mcp на порту $port: $code"
  fi
}

echo "" > "$LOG"
log "============================================"
log "Старт поэтапного запуска MCP-сервисов"
log "============================================"

# 1) erp_main — через 2.5 часа
log "Ожидание 2ч 30мин перед запуском erp_main..."
sleep 9000
start_service "1c-metacode-erp_main" 6001

# 2) ssl3 — через 3.5 часа после erp_main
log "Ожидание 3ч 30мин перед запуском ssl3..."
sleep 12600
start_service "1c-metacode-ssl3" 6003

# 3) do_ame — через 1 час после ssl3
log "Ожидание 1ч перед запуском do_ame..."
sleep 3600
start_service "1c-metacode-do_ame" 6005

# 4) erp_ame_do — через 1 час после do_ame
log "Ожидание 1ч перед запуском erp_ame_do..."
sleep 3600
start_service "1c-metacode-erp_ame_do" 6002

log "============================================"
log "Все сервисы запущены!"
log "============================================"
