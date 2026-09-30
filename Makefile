# Весь интерфейс проекта: `make` — список команд.
SHELL := /bin/bash
.DEFAULT_GOAL := help
export APP_UID := $(shell id -u)
export APP_GID := $(shell id -g)
DC := docker compose
EXEC := $(DC) exec -T api
TIMER := /usr/bin/python3 tools/timer.py
M ?= gpt-6-luna
E ?= medium
START_BRANCH := $(word 2,$(MAKECMDGOALS))
export START_BRANCH

# `make start <name>`: имя ветки также передаётся make как отдельная цель.
ifneq ($(filter start,$(MAKECMDGOALS)),)
%:
	@:
endif

.PHONY: help init start stop up build logs migration migrate psql demo test fmt ai timer

help: ## Список команд
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[36m%-11s\033[0m %s\n", $$1, $$2}'

init: ## Один раз: локальный .venv (для IDE) и сборка образа
	uv sync
	$(DC) build
	@echo 'Дальше: make up && make demo; make ai — один раз одобрить хуки ai-logs в /hooks'

start: ## СТАРТ: make start <name> — ветка <name>, чистые ai-logs, сервисы, таймер с 00:00
	@[[ $(words $(MAKECMDGOALS)) -eq 2 ]] && [[ -n "$$START_BRANCH" ]] || \
		{ echo 'usage: make start <name>'; exit 2; }
	@git check-ref-format --branch "$$START_BRANCH" >/dev/null || exit 2
	@case "$$START_BRANCH" in \
		main|master|help|init|start|stop|up|build|logs|migration|migrate|psql|demo|test|fmt|ai|timer) \
			echo 'Имя интервью не должно совпадать с веткой main или make-командой'; exit 2;; \
	esac
	@branch="$$(git branch --show-current)"; active="$$(cat .local/interview-branch 2>/dev/null || true)"; \
	if [[ -n "$$active" && "$$active" == "$$START_BRANCH" && "$$branch" == "$$active" ]]; then \
		echo "Интервью уже идёт: поднимаю сервисы и открываю таймер"; \
		$(DC) up -d --wait && $(TIMER) open; \
	elif [[ -n "$$active" ]]; then \
		echo "Сначала заверши интервью в ветке $$active: make stop"; exit 2; \
	else \
		git switch -c "$$START_BRANCH" && python3 tools/ai_log.py clean && $(DC) up -d --wait && \
		mkdir -p .local && printf '%s\n' "$$START_BRANCH" > .local/interview-branch && $(TIMER) start && \
		echo "Открой НОВУЮ сессию агента (make ai): хуки ai-logs подхватываются при старте сессии"; \
	fi

stop: ## ФИНИШ: commit + push ветки интервью, стереть БД/S3, остановить всё, вернуться на main
	@branch="$$(git branch --show-current)"; active="$$(cat .local/interview-branch 2>/dev/null || true)"; \
	if [[ -n "$$active" && "$$branch" != "$$active" ]]; then \
		echo "Интервью идёт в ветке $$active, а сейчас $$branch: git switch $$active"; exit 2; \
	fi; \
	if [[ -n "$$active" ]]; then \
		git ls-files -co --exclude-standard -z | python3 tools/ai_log.py scan - || exit 1; \
		$(TIMER) stop >/dev/null; \
		git add -A && { git diff --cached --quiet || git commit -q -m "Interview: $$active"; } && \
		git push -u origin HEAD || { echo 'push не прошёл: почини и повтори make stop'; exit 1; }; \
	fi; \
	$(TIMER) stop >/dev/null; \
	$(DC) down -v --remove-orphans || exit 1; \
	if [[ -n "$$active" ]]; then \
		git switch main && rm -f .local/interview-branch && echo "Готово: $$active запушена, данные стёрты, ветка main"; \
	fi

up: ## Поднять всё: PostgreSQL, Redis, RustFS, api, worker (+ миграции)
	$(DC) up -d --wait
	@$(DC) ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'

build: ## Пересобрать образ после `uv add <пакет>` и перезапустить
	$(DC) build
	$(MAKE) up

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

ai: ## Codex Fast: make ai [M=gpt-6.1-sol] [E=low|medium|high]; по умолчанию gpt-6-luna medium
	codex -m $(M) -c 'model_reasoning_effort="$(E)"' -c 'plan_mode_reasoning_effort="$(E)"' -c 'service_tier="fast"'

timer: ## Открыть таймер без сброса; тест: make timer SPEED=60 [AT=35]
	@$(TIMER) $(if $(SPEED)$(AT),start --speed $(or $(SPEED),1) --at $(or $(AT),0),open)
