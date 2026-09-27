# MVP за 45 минут

Интервью AI System Design. Цель — за ~45 минут показать работающий MVP через curl: happy path и
интеграция с критичными компонентами. Production-архитектура и НФТ (RPS, отказоустойчивость,
масштабирование) описаны в spec.md; таблицы, связи, ручки и шаги MVP — в plan.md.
В коде production-требования осознанно НЕ обеспечиваем.
**Скорость важнее полноты. Простой код важнее «правильного».**

## Как работать
- Отвечай по-русски и коротко. Имена в коде и API — английские.
- Делай ровно заказанный шаг и сразу пиши код. Не пересказывай план, не исследуй репозиторий:
  структура описана ниже, образец фичи — `app/api/items.py`.
- Не задавай вопросов, если можно принять простое допущение: прими его и назови в отчёте.
- Код синхронный (`def`, `SessionDep`). async — только если об этом явно попросили.
- Тесты не пиши, если их явно не попросили. Проверка шага — curl.
- Не добавляй слои (Service/Repository/Protocol), зависимости, сервисы, авторизацию, валидацию
  сверх нужной, если не попросили. 20 строк в обработчике лучше трёх слоёв.
- Не используй субагентов, skills и веб-поиск.
- Что-то упало — почини сам (до 2 попыток), затем коротко опиши, что мешает.

## Замены prod → MVP (по умолчанию)
| В дизайне | В MVP |
|---|---|
| Elasticsearch / OpenSearch + CDC | PostgreSQL + pg_trgm: `ILIKE '%q%'` + GIN-индекс (как `items.title`) |
| Kafka / RabbitMQ / outbox / Debezium | таблица `jobs` + воркер: `queue.enqueue(...)` в той же транзакции |
| Микросервисы | один сервис `app` (модули в `app/api/`) + `worker` |
| Шардирование, реплики, партиции | одна PostgreSQL |
| S3 | RustFS через `app/infra/storage.py` |
| OAuth / SSO | нет; если нужен пользователь — `CurrentUser` из `app/auth.py` (JWT) |
| WebSocket / push-уведомления | polling: GET-ручка статуса |
| ClickHouse / аналитика | SQL-агрегаты в PostgreSQL |
| Внешние API (платежи, SMS, партнёры) | функция-заглушка; реальный вызов — `app/infra/http.py` |
| Cron / отложенные задачи | `queue.enqueue(..., delay_s=...)` |
| Распределённый лок, идемпотентность | `cache.once(key)` (Redis SET NX) или UNIQUE в PostgreSQL |

## Структура
```
app/main.py         FastAPI: подключение роутеров, /health
app/models.py       все таблицы SQLAlchemy
app/schemas.py      Pydantic-схемы
app/api/<name>.py   ручки; новый роутер подключить в app/main.py
app/jobs.py         обработчики фоновых задач: функция(session, payload) + запись в HANDLERS
app/worker.py       пул воркеров (не трогать)
app/auth.py         JWT: POST /api/auth/token, зависимость CurrentUser
app/db.py           SessionDep (sync), AsyncSessionDep (async)
app/infra/cache.py    Redis: get_json, set_json, delete, once, rate_limit
app/infra/storage.py  S3: put, get, presign
app/infra/queue.py    enqueue (очередь в PostgreSQL)
app/infra/http.py     post_json с ретраями (tenacity)
migrations/         Alembic
demo.sh             curl-сценарий happy path (make demo)
```

## Цикл шага
1. Код. Всё уже запущено (`make up`), api и worker перезагружаются сами.
2. Менял `app/models.py` → `make migration m="что поменял"` (создаёт и применяет миграцию).
   Новое NOT NULL поле в существующей таблице — с `server_default`, как `Item.status`.
3. Проверь curl'ом на `http://localhost:8000`; при ошибке смотри `make logs`.
4. Отчёт до 5 строк: что сделано; команда проверки и её результат; допущения и упрощения.

## Команды
`make up` · `make logs` · `make migration m="..."` · `make migrate` · `make psql` · `make demo` ·
`make test` (только если есть тесты). Новая зависимость: `uv add <pkg>`, затем `make build`.

## Нельзя
- Читать и менять `manual/` (код без AI) и `ai-logs/` (пишут хуки).
- `docker compose down -v`, `make reset`, удалять данные; делать commit/push без просьбы.
- Менять spec.md и plan.md без просьбы. Если просят заполнить spec.md — сохраняй шаблон,
  пиши кратко: ячейка таблицы — до 12 слов. В plan.md указывай конкретные поля PostgreSQL
  с типами и ограничениями, связи, HTTP-ручки, важные нюансы и отдельные проверяемые шаги.
