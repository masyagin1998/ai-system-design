# Весь интерфейс проекта: `make` — список команд.
SHELL := /bin/bash
.DEFAULT_GOAL := help
export APP_UID := $(shell id -u)
export APP_GID := $(shell id -g)
DC := docker compose
EXEC := $(DC) exec -T api
TIMER := /usr/bin/python3 tools/timer.py
E ?= low
START_BRANCH := $(word 2,$(MAKECMDGOALS))
export START_BRANCH

# `make start <branch-name>`: имя ветки также передаётся make как отдельная цель.
ifneq ($(filter start,$(MAKECMDGOALS)),)
%:
	@:
endif

.PHONY: help init up build down logs migration migrate psql demo test fmt reset ai timer timer-stop start finish

help: ## Список команд
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-11s\033[0m %s\n", $$1, $$2}'

init: ## Один раз: локальный .venv (для IDE) и сборка образа
	uv sync
	$(DC) build

up: ## Поднять всё: PostgreSQL, Redis, RustFS, api, worker (+ миграции)
	$(DC) up -d --wait
	@$(DC) ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'

build: ## Пересобрать образ после `uv add <пакет>` и перезапустить
	$(DC) build
	$(MAKE) up

down: ## Остановить (данные сохраняются)
	$(DC) down

logs: ## Логи api и worker за последние 5 минут
	$(DC) logs --since 5m --tail 200 api worker

migration: ## Миграция по models.py + применить: make migration m="add links"
	@test -n "$(m)" || { echo 'usage: make migration m="что поменял"'; exit 2; }
	$(EXEC) alembic revision --autogenerate -m "$(m)"
	$(EXEC) alembic upgrade head

migrate: ## Применить миграции
	$(EXEC) alembic upgrade head

psql: ## Консоль PostgreSQL
	$(DC) exec postgres psql -U app app

demo: ## Happy path через curl
	@bash demo.sh

test: ## Тесты в контейнере: make test [k=items]
	$(EXEC) pytest $(if $(k),-k "$(k)")

fmt: ## Форматирование и автоисправления ruff
	$(EXEC) ruff format .
	$(EXEC) ruff check --fix .

reset: ## УДАЛИТЬ все данные (volumes) и поднять заново
	$(DC) down -v
	$(MAKE) up

ai: ## Codex GPT-6-Luna Fast: make ai [E=low|medium|high], по умолчанию low
	codex -m gpt-6-luna -c 'model_reasoning_effort="$(E)"' -c 'plan_mode_reasoning_effort="$(E)"' -c 'service_tier="fast"'

timer: ## Открыть таймер без сброса; тест: make timer SPEED=60 [AT=33]
	@$(TIMER) $(if $(SPEED)$(AT),start --speed $(or $(SPEED),1) --at $(or $(AT),0),open)

timer-stop: ## Остановить и закрыть таймер
	@$(TIMER) stop

start: ## СТАРТ интервью: make start <branch-name>; чистые ai-logs, таймер с 00:00
	@[[ $(words $(MAKECMDGOALS)) -eq 2 ]] && [[ -n "$$START_BRANCH" ]] || \
		{ echo 'usage: make start <branch-name>'; exit 2; }
	@git check-ref-format --branch "$$START_BRANCH" >/dev/null || exit 2
	@case "$$START_BRANCH" in \
		main|master|dev|test|prod|help|init|up|build|down|logs|migration|migrate|psql|demo|fmt|reset|ai|timer|timer-stop|start|finish) \
			echo 'Укажи отдельную ветку для интервью, не совпадающую с make-командой'; exit 2;; \
	esac
	@branch="$$(git branch --show-current)"; active="$$(cat .local/interview-branch 2>/dev/null || true)"; \
	if $(TIMER) active && [[ "$$branch" == "$$START_BRANCH" && "$$active" == "$$START_BRANCH" ]]; then \
		echo "Интервью уже идёт: открываю таймер"; $(TIMER) open; \
	elif $(TIMER) active && [[ -n "$$active" ]]; then \
		echo "Сначала останови тренировку в ветке $$active: make timer-stop"; exit 2; \
	else \
		git switch -c "$$START_BRANCH" && \
		python3 tools/ai_log.py clean && $(TIMER) start && \
		mkdir -p .local && printf '%s\n' "$$START_BRANCH" > .local/interview-branch && \
		echo "Открой НОВУЮ сессию агента (make ai): хуки ai-logs подхватываются при старте сессии"; \
	fi

finish: ## ФИНИШ: проверка секретов, commit и push ветки активной тренировки
	@branch="$$(git branch --show-current)"; active="$$(cat .local/interview-branch 2>/dev/null || true)"; \
	[[ -n "$$active" && "$$branch" == "$$active" ]] || \
		{ echo "Не ветка тренировки: сначала make start <branch-name>"; exit 2; }
	@git ls-files -co --exclude-standard -z | python3 tools/ai_log.py scan -
	git add -A
	git diff --cached --quiet || git commit -m "Interview result"
	git push -u origin HEAD
