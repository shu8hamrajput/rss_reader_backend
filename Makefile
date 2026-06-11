.DEFAULT_GOAL := help
VENV         := .venv
PYTHON       := $(VENV)/bin/python
PIP          := $(VENV)/bin/pip
APP          := rss-reader-api

# ── Help ──────────────────────────────────────────────────────────────────────

.PHONY: help
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}' | sort

# ── Local dev ─────────────────────────────────────────────────────────────────

.PHONY: install
install: ## Install dependencies into .venv
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

.PHONY: dev
dev: ## Run dev server with auto-reload (port 8080)
	$(PYTHON) -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload

.PHONY: run
run: ## Run production server locally (no reload, single worker)
	$(PYTHON) -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 1

.PHONY: test
test: ## Run the test suite (requires Postgres + Redis running, e.g. `make up-d`)
	$(PYTHON) -m pytest

.PHONY: worker
worker: ## Run a Celery worker (executes the periodic feed-refresh task)
	$(PYTHON) -m celery -A app.celery_app worker --loglevel=info

.PHONY: beat
beat: ## Run Celery beat (schedules the periodic feed-refresh task every 30 min)
	$(PYTHON) -m celery -A app.celery_app beat --loglevel=info

# ── Parser generator (app.services.parser_gen) ────────────────────────────────

.PHONY: gen-parser
gen-parser: ## Generate a candidate fetcher  →  make gen-parser URL=https://example.com/feed [LLM=1] [SAMPLES=3]
	$(PYTHON) -m app.services.parser_gen generate "$(URL)" $(if $(LLM),--llm) $(if $(SAMPLES),--samples $(SAMPLES))

.PHONY: improve-parser
improve-parser: ## Refine a candidate/active fetcher  →  make improve-parser SLUG=example_com [LLM=1] [FEEDBACK="..."] [URL=...]
	$(PYTHON) -m app.services.parser_gen improvise "$(SLUG)" $(if $(LLM),--llm) $(if $(FEEDBACK),--feedback "$(FEEDBACK)") $(if $(URL),--url "$(URL)")

.PHONY: approve-parser
approve-parser: ## Promote a candidate to active  →  make approve-parser SLUG=example_com
	$(PYTHON) -m app.services.parser_gen approve "$(SLUG)"

.PHONY: process-parser-requests
process-parser-requests: ## Generate/refine candidates from pending user "request better parser" submissions  →  make process-parser-requests [LLM=1] [SAMPLES=3]
	$(PYTHON) -m app.services.parser_gen process-requests $(if $(LLM),--llm) $(if $(SAMPLES),--samples $(SAMPLES))

# ── Docker Compose (Postgres + Redis + RabbitMQ + API + worker + beat) ───────

.PHONY: up
up: ## Start the full stack (Postgres, Redis, RabbitMQ, API, worker, beat)
	docker compose up --build

.PHONY: up-d
up-d: ## Start the full stack in the background
	docker compose up --build -d

.PHONY: down
down: ## Stop and remove the full stack
	docker compose down

.PHONY: down-v
down-v: ## Stop the stack and delete its volumes (Postgres/Redis/RabbitMQ data)
	docker compose down -v

.PHONY: compose-logs
compose-logs: ## Tail logs from every service in the stack
	docker compose logs -f

# ── Docker (single image) ─────────────────────────────────────────────────────

.PHONY: docker-build
docker-build: ## Build Docker image tagged as $(APP):latest
	docker build -t $(APP):latest .

.PHONY: docker-stop
docker-stop: ## Stop all running containers for this image
	docker ps -q --filter ancestor=$(APP):latest | xargs -r docker stop

# ── Fly.io ────────────────────────────────────────────────────────────────────

.PHONY: deploy-prod
deploy-prod: ## Deploy to Fly.io (production)
	fly deploy --app $(APP)

.PHONY: deploy-local
deploy-local: ## Build image locally then deploy (avoids remote builder)
	fly deploy --app $(APP) --local-only

.PHONY: status
status: ## Show app status and recent deployment info
	fly status --app $(APP)

.PHONY: logs
logs: ## Tail live application logs
	fly logs --app $(APP)

.PHONY: ssh
ssh: ## Open an interactive SSH session on the running machine
	fly ssh console --app $(APP)

.PHONY: secrets-list
secrets-list: ## List secret names set on Fly (values are hidden)
	fly secrets list --app $(APP)

.PHONY: secrets-set
secrets-set: ## Set secrets from a local .env file  →  make secrets-set ENV=.env
	fly secrets import --app $(APP) < $(ENV)

.PHONY: scale
scale: ## Show current VM count and size
	fly scale show --app $(APP)

# ── Database (PostgreSQL) ─────────────────────────────────────────────────────

.PHONY: db-shell
db-shell: ## Open a psql shell against $(DATABASE_URL) (defaults to local docker-compose Postgres)
	docker compose exec postgres psql -U postgres -d rss_reader

.PHONY: db-shell-remote
db-shell-remote: ## Open a psql shell on the remote Fly Postgres app  →  make db-shell-remote APP=rss-reader-db
	fly postgres connect --app $(APP)

# ── Health ────────────────────────────────────────────────────────────────────

.PHONY: health
health: ## Hit the /health endpoint on the deployed app
	curl -sf https://$(APP).fly.dev/health | python3 -m json.tool

.PHONY: health-local
health-local: ## Hit the /health endpoint on localhost:8080
	curl -sf http://localhost:8080/health | python3 -m json.tool
