# ERP AI Agent -- developer tasks.
#
# Mirrors RAG/Makefile so the two packages are driven the same way. On Windows
# these run under Git Bash; the underlying commands are plain `uv run ...` and
# can be typed directly if `make` is absent.

.DEFAULT_GOAL := help
UV := uv run

.PHONY: help install fmt fmt-check lint types arch test test-unit test-integration test-security cov check clean role

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Create the virtualenv and install the project with dev extras
	uv venv --python 3.14
	uv pip install -e ".[dev]"

fmt: ## Format the codebase
	$(UV) ruff format src tests
	$(UV) ruff check --fix src tests

fmt-check: ## Verify formatting without modifying files
	$(UV) ruff format --check src tests

lint: ## Lint the codebase
	$(UV) ruff check src tests

types: ## Static type check
	$(UV) mypy

arch: ## Enforce the layering contracts
	$(UV) lint-imports --config importlinter.ini

test: ## Run the full test suite
	$(UV) pytest

test-unit: ## Run only the fast, hermetic unit tests
	$(UV) pytest -m unit

test-integration: ## Run the tests that need a live database
	$(UV) pytest -m integration

test-security: ## Run the security gates
	$(UV) pytest -m security

cov: ## Run tests with a coverage report
	$(UV) pytest --cov --cov-report=term-missing

check: fmt-check lint types arch test ## Everything CI runs

role: ## (Re-)apply the read-only role to the running mock database
	docker exec mock-hr-postgres psql -U hr_user -d mock_hr \
		-v ON_ERROR_STOP=1 -f /database/schema/hr/10_readonly_role.sql

reingest: ## Re-ingest the test corpus with Phase 6 security metadata
	$(UV) python scripts/reingest_documents.py \
		--manifest tests/fixtures/documents.yaml \
		--directory tests/fixtures/documents \
		--module HR \
		--record tests/fixtures/documents/ingested.json

clean: ## Remove caches and build artefacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .import_linter_cache htmlcov .coverage dist build
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
