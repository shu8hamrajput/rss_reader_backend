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

# ── Docker ────────────────────────────────────────────────────────────────────

.PHONY: docker-build
docker-build: ## Build Docker image tagged as $(APP):latest
	docker build -t $(APP):latest .

.PHONY: docker-run
docker-run: ## Run Docker image locally (port 8080, ephemeral DB)
	docker run --rm -p 8080:8080 \
		-e DATABASE_URL=sqlite:////data/rss_reader.db \
		-v $(PWD)/data:/data \
		$(APP):latest

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

# ── Database (Fly volume / SQLite) ────────────────────────────────────────────

.PHONY: db-shell
db-shell: ## Open SQLite shell on the remote Fly volume
	fly ssh console --app $(APP) --command "sqlite3 /data/rss_reader.db"

.PHONY: db-download
db-download: ## Download a copy of the remote DB to ./rss_reader.db.bak
	fly ssh sftp get --app $(APP) /data/rss_reader.db rss_reader.db.bak
	@echo "Saved to rss_reader.db.bak"

# ── Health ────────────────────────────────────────────────────────────────────

.PHONY: health
health: ## Hit the /health endpoint on the deployed app
	curl -sf https://$(APP).fly.dev/health | python3 -m json.tool

.PHONY: health-local
health-local: ## Hit the /health endpoint on localhost:8080
	curl -sf http://localhost:8080/health | python3 -m json.tool
