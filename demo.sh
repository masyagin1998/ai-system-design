#!/usr/bin/env bash
# Happy path через curl: make demo. Для новой задачи замени шаги ниже на свой сценарий.
set -euo pipefail
API=${API:-http://localhost:8000}
step() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
# Любой ответ 4xx/5xx останавливает сценарий с ошибкой (--fail-with-body + set -e).
call() { curl -sS --fail-with-body -H 'Content-Type: application/json' -w '\n← HTTP %{http_code}\n' "$@"; }  # call -X POST "$API/..." -d '{...}'
json() { curl -sS --fail-with-body -H 'Content-Type: application/json' "$@"; }  # тело без статуса

step "Health: PostgreSQL + Redis + S3 (sync) и async-стек"
call "$API/health"
call "$API/health/async"

step "Создать item (в той же транзакции ставится job item.created)"
ITEM=$(json -X POST "$API/api/items" -d '{"title":"Привет, мир"}')
echo "$ITEM"
ID=$(jq -er .id <<<"$ITEM")

step "Через секунду воркер отметил item как processed"
sleep 1.5
call "$API/api/items/$ID"

step "Поиск по подстроке (pg_trgm)"
call "$API/api/items?q=%D0%BC%D0%B8%D1%80&limit=5"  # q=мир

step "Загрузить файл в S3 и скачать обратно"
KEY=$(echo "hello s3" | curl -sS --fail-with-body -F "file=@-;filename=hello.txt" "$API/api/files" | jq -er .key)
echo "key=$KEY"
call "$API/api/files/$KEY"

step "JWT: получить токен и вызвать защищённую ручку"
TOKEN=$(json -X POST "$API/api/auth/token" -d '{"user_id":42}' | jq -er .access_token)
call -H "Authorization: Bearer $TOKEN" "$API/api/auth/me"
