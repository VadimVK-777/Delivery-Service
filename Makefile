.DEFAULT_GOAL := help
POETRY ?= poetry
RUN = $(POETRY) run
CODE ?= src
TEST_ARGS ?=
DATE ?=
ID ?=

.PHONY: help install env hooks lint format-check format fix typecheck security metrics quality test test-unit test-integration check check-all ci pre-commit up down logs migrate worker retry outbox-once index-knowledge report verify-report load

help:
	@echo make env install hooks - Prepare configuration, dependencies and Git hooks
	@echo make check             - Quality, unit/integration/browser tests, metrics
	@echo make quality           - Ruff, formatting, mypy and Bandit
	@echo make test-unit         - Fast tests without Docker
	@echo make test-integration  - Disposable Docker stack and full test suite
	@echo make fix               - Ruff auto-fixes and formatting
	@echo make up / down / logs  - Manage application Docker Compose stack
	@echo make report DATE=2026-01-01 - Daily MongoDB totals
	@echo make retry ID=uuid     - Requeue a persisted parcel
	@echo Individual checks: lint format-check typecheck security metrics pre-commit

install:
	$(POETRY) install --with dev
env:
	$(RUN) python scripts/init_env.py
hooks:
	$(RUN) pre-commit install
lint:
	$(RUN) ruff check src tests scripts migrations
format-check:
	$(RUN) ruff format --check src tests scripts migrations
format:
	$(RUN) ruff format src tests scripts migrations
fix:
	$(RUN) ruff check --fix src tests scripts migrations
	$(RUN) ruff format src tests scripts migrations
typecheck:
	$(RUN) mypy $(CODE)
security:
	$(RUN) bandit -c pyproject.toml -r $(CODE)
metrics:
	$(RUN) radon cc --show-complexity --average $(CODE)
	$(RUN) radon mi --show $(CODE)
test-unit:
	$(RUN) pytest tests/unit $(TEST_ARGS)
test-integration:
	$(RUN) python scripts/integration.py
test: test-unit test-integration
quality: lint format-check typecheck security
check: quality test metrics pre-commit
check-all: check
ci: check
pre-commit:
	$(RUN) pre-commit run --all-files
up:
	docker compose up --build -d
down:
	docker compose down
logs:
	docker compose logs -f api worker outbox
migrate:
	docker compose run --rm migrate
worker:
	$(RUN) delivery worker
retry:
	docker compose exec worker delivery retry-parcel $(ID)
outbox-once:
	docker compose run --rm outbox delivery outbox --once
index-knowledge:
	docker compose run --rm knowledge-init
report:
	docker compose exec outbox delivery report-daily --date $(DATE)
verify-report:
	docker compose exec outbox delivery verify-report --date $(DATE)
load:
	$(RUN) python scripts/load_test.py $(TEST_ARGS)
