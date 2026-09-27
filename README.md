# AI System Design — шаблон MVP

Шаблон для секции AI System Design (2 часа): ревью RFC джуна → работающий MVP через curl →
код руками. Заточен под GPT-6-Luna Fast на Low/Medium: агент сразу пишет простой синхронный
код по готовым образцам, а production-архитектура и НФТ живут в [spec.md](spec.md) и [plan.md](plan.md).

**Стек:** FastAPI · SQLAlchemy 2 (sync + async, psycopg 3) · Alembic · PostgreSQL 18 + pg_trgm ·
Redis · RustFS (S3) · очередь задач в PostgreSQL + пул воркеров · httpx + tenacity · JWT.

## Подготовка (до интервью)

```bash
make init              # .venv для IDE + сборка образа
make up                # PostgreSQL, Redis, RustFS, api, worker; миграции
make demo              # happy path через curl — всё зелёное
make ai                # Codex: один раз одобрить хуки ai-logs в /hooks, затем выйти
make timer SPEED=60    # посмотреть таймер (2 часа за 2 минуты); make timer-stop
```

## На интервью

1. `make start` — ветка `interview-*`, чистые `ai-logs/`, таймер с 00:00.
2. `make ai` — **новая** сессия Codex: хуки ai-logs подхватываются только при старте сессии.
3. **Дизайн (0–35):** ревью RFC → [spec.md](spec.md) (ФТ, НФТ, расчёты) → [plan.md](plan.md)
   (prod → MVP, API, таблицы, шаги для агента). Как только ясны таблицы, шаг 1 можно отдать агенту.
4. **Реализация (35–80):** «Сделай шаг N из plan.md» → проверить curl → следующий шаг.
5. **Код руками (80–100):** `manual/solution.py`, `python3 manual/solution.py`. Агент в это время
   может доделывать MVP.
6. **Финал (110–120):** `make finish` — проверка секретов, commit, push ветки.

## Промпты

| Когда | Промпт | Effort |
|---|---|---|
| Дизайн | «Заполни spec.md по RFC ниже: только таблицы, коротко.» + текст RFC | TBD |
| Дизайн | «Заполни plan.md по spec.md: API, таблицы, prod → MVP, 3–5 шагов.» | TBD |
| Реализация | «Сделай шаг 2 из plan.md.» | TBD |
| Реализация | «Добавь поле X в Y: модель, миграция, API. Проверь curl.» | TBD |
| Ошибка | «`curl …` вернул 500, в `make logs`: <traceback>. Почини.» | TBD |
| Финал | «Допиши в plan.md раздел „Не сделано / риски“ по факту кода.» | TBD |

Запуск с нужным уровнем: `make ai E=low` (или `medium`, `high`); в открытой сессии — `/model`.

## Команды

| Команда | Что делает |
|---|---|
| `make up` / `make down` | поднять всё / остановить (данные сохраняются) |
| `make build` | пересобрать образ после `uv add <пакет>` |
| `make logs` | логи api и worker за 5 минут |
| `make migration m="..."` | миграция по `app/models.py` + применить |
| `make migrate` · `make psql` | применить миграции · консоль PostgreSQL |
| `make demo` · `make test` · `make fmt` | curl-сценарий · pytest · ruff |
| `make reset` | **удалить данные** и поднять заново |
| `make ai [E=low\|medium\|high]` | Codex GPT-6-Luna Fast |
| `make timer [SPEED=60 AT=33]` · `make timer-stop` | таймер (без сброса) · остановить |
| `make start` · `make finish` | старт интервью · commit + push результата |

API: http://localhost:8000/docs · консоль S3: http://localhost:9001 (rustfsadmin / rustfsadmin).

## Как устроено

- **Код** — `app/`: образец фичи `api/items.py` (CRUD, cache-aside в Redis, поиск через pg_trgm,
  фоновая задача), `api/files.py` (S3), `auth.py` (JWT), готовые помощники в `infra/`.
  Правила для агента — [AGENTS.md](AGENTS.md) (`CLAUDE.md` импортирует его).
- **Очередь вместо Kafka** — таблица `jobs`: `queue.enqueue()` в той же транзакции (outbox даром),
  воркер забирает задачи через `FOR UPDATE SKIP LOCKED`, повторяет с backoff, после 5 попыток — `failed`.
- **Таймер** — окно без рамки поверх всех окон (и fullscreen) на основном мониторе: этап,
  подэтап с подсказкой, обратный отсчёт, контрольные точки 33' / 78' / 100', шкала двух часов.
  Тащится мышью; двойной клик — компактный режим; ПКМ — пауза, ±1 минута, сброс.
- **ai-logs** — хуки Codex и Claude пишут `ai-logs/PROMPTS.md` и `ai-logs/sessions/` с этапом
  таймера в каждом заголовке и маскированием секретов.
- **Codex** — `.codex/config.toml`: Luna Fast Medium, без подтверждений, без субагентов, плагинов,
  MCP и веб-поиска (меньше системного промпта — быстрее ответы).
