PYTHON ?= .venv/bin/python
PIP ?= $(PYTHON) -m pip
UV ?= uv
APP ?= app.asgi:app

.PHONY: help setup install install-llm install-all lint format typecheck test test-unit test-integration test-e2e test-contract coverage security run check-config check-secrets build clean

help:
	@echo "Targets:"
	@echo "  setup             Create .venv if missing and install dev dependencies"
	@echo "  install           Install project in editable mode with dev deps"
	@echo "  install-llm       Install optional Anthropic/Redis/Vault/AWS extras"
	@echo "  lint              Run ruff checks"
	@echo "  format            Run ruff formatter"
	@echo "  typecheck         Run mypy"
	@echo "  test              Run unit + integration tests with coverage"
	@echo "  test-unit         Run fast unit tests"
	@echo "  test-integration  Run integration tests"
	@echo "  test-e2e          Run end-to-end tests"
	@echo "  test-contract     Run external API contract tests"
	@echo "  coverage          Run full test suite with coverage report"
	@echo "  security          Run bandit and pip-audit"
	@echo "  run               Start the API locally"
	@echo "  check-config      Validate the active environment config"
	@echo "  check-secrets     Verify required secrets for the active environment"
	@echo "  build             Build wheel and sdist"
	@echo "  clean             Remove local caches and build artifacts"

setup:
	@test -x .venv/bin/python || python3.15 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

install:
	$(PIP) install -e ".[dev]"

install-llm:
	$(PIP) install -e ".[dev,anthropic,redis,vault,aws]"

lint:
	$(PYTHON) -m ruff check src tests

format:
	$(PYTHON) -m ruff format src tests

typecheck:
	$(PYTHON) -m mypy src

test: test-unit test-integration

test-unit:
	$(PYTHON) -m pytest -m unit

test-integration:
	$(PYTHON) -m pytest -m integration

test-e2e:
	$(PYTHON) -m pytest -m e2e

test-contract:
	$(PYTHON) -m pytest -m contract

coverage:
	$(PYTHON) -m pytest --cov=src/app --cov-report=term-missing --cov-report=xml

security:
	$(PYTHON) -m bandit -q -r src
	$(PYTHON) -m pip_audit

run:
	$(PYTHON) -m uvicorn $(APP) --host 0.0.0.0 --port 8000 --reload

check-config:
	$(PYTHON) -m app.cli check-config

check-secrets:
	$(PYTHON) -m app.cli check-secrets

build:
	$(PYTHON) -m build

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov dist build *.egg-info coverage.xml .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
