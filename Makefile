# Echo — dev locally, deploy to a single server over SSH. The image is built
# from a repo clone on the server; a stack manager (e.g. Dockge) or plain
# `docker compose` owns the running container.
#
# The defaults below are PLACEHOLDERS. Put your real host/paths in a local,
# git-ignored `deploy.local.mk` (see deploy.local.mk.example) — it's included
# automatically and overrides these. Nothing host-specific lives in this file.
DEPLOY_HOST ?= you@your-server
DEPLOY_PATH ?= /opt/echo/app
DOCKGE_STACK ?= /opt/dockge/stacks/echo
SMOKE_URL ?= http://localhost:7338
-include deploy.local.mk

REPO_URL := $(shell git remote get-url origin 2>/dev/null)
# prefer the repo venv (has pytest etc.); fall back to system python3
PYTHON := $(shell test -x .venv/bin/python && echo .venv/bin/python || echo python3)

.PHONY: help dev test build deploy setup logs status smoke

help:
	@echo ""
	@echo "Echo — available targets:"
	@echo ""
	@echo "  Development"
	@echo "    make dev        Run API locally with hot reload (reads .env)"
	@echo "    make test       Run test suite"
	@echo ""
	@echo "  Deployment (set your host/paths in deploy.local.mk first)"
	@echo "    make setup      One-time: clone repo on the server, ship stack compose + .env"
	@echo "    make deploy     test + push + pull on the server + build image + recreate stack"
	@echo "    make logs       Tail the echo container logs"
	@echo "    make status     docker compose ps for the stack"
	@echo "    make smoke      Hit /api/health"
	@echo ""

# ---------------------------------------------------------------------------
# Development
# ---------------------------------------------------------------------------

dev:
	set -a && source .env && set +a && \
		uvicorn echo.main:app --reload --port 7338

test:
	$(PYTHON) -m pytest tests/ -q

# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------

build:
	docker build -t echo:latest .

# NOTE: remote commands run via `zsh -lc` because non-interactive SSH on macOS
# hosts doesn't run path_helper, so docker (/usr/local/bin) isn't on PATH
# otherwise. On a Linux server you can drop the `zsh -lc` wrapper.
deploy: test
	git push
	# fetch + hard-reset (not pull): the host is a build mirror, so it must match
	# the pushed commit exactly — immune to force-pushes / history rewrites and to
	# any stray local edits on the host. NOTE: this discards uncommitted changes
	# in $(DEPLOY_PATH), which is intended for a deploy clone.
	ssh -t $(DEPLOY_HOST) 'zsh -lc "cd $(DEPLOY_PATH) && git fetch origin && git reset --hard origin/main && \
		docker build -t echo:latest . && \
		cd $(DOCKGE_STACK) && docker compose up -d"'
	@sleep 3 && $(MAKE) smoke

setup:
	@test -f .env || (echo "create .env first: cp .env.example .env, fill in the key"; exit 1)
	@test -n "$(REPO_URL)" || (echo "no git remote 'origin' — create the GitHub repo first"; exit 1)
	ssh $(DEPLOY_HOST) 'mkdir -p $(DOCKGE_STACK) && \
		test -d $(DEPLOY_PATH)/.git || git clone $(REPO_URL) $(DEPLOY_PATH)'
	scp deploy/dockge-compose.yml $(DEPLOY_HOST):$(DOCKGE_STACK)/compose.yaml
	scp .env $(DEPLOY_HOST):$(DOCKGE_STACK)/.env
	ssh -t $(DEPLOY_HOST) 'zsh -lc "cd $(DEPLOY_PATH) && docker build -t echo:latest . && \
		cd $(DOCKGE_STACK) && docker compose up -d"'
	@sleep 3 && $(MAKE) smoke

logs:
	ssh $(DEPLOY_HOST) 'zsh -lc "docker logs -f --tail 100 echo"'

status:
	ssh $(DEPLOY_HOST) 'zsh -lc "cd $(DOCKGE_STACK) && docker compose ps"'

smoke:
	@curl -sf -m 5 $(SMOKE_URL)/api/health || echo "health check FAILED"
	@echo
