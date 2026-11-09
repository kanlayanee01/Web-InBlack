TESTER = docker compose --profile test run --rm --build tester
E2E = docker compose --profile e2e run --rm --build e2e

.PHONY: up down stack-test lint typecheck test test-unit test-integration test-e2e chaos perf lighthouse

up:
	docker compose up --build

down:
	docker compose --profile test --profile e2e down -v

# API tests wipe the database, and /_test/reset only exists when APP_ENV=test.
stack-test:
	APP_ENV=test docker compose up --build -d --wait

lint:
	$(TESTER) ruff check app tests alembic

typecheck:
	$(TESTER) mypy

test-unit:
	$(TESTER) pytest tests/unit -q

test-integration: stack-test
	APP_ENV=test $(TESTER) pytest tests/integration tests/api -q

test-e2e: stack-test
	APP_ENV=test $(E2E)

test: lint typecheck test-unit test-integration test-e2e

chaos: stack-test
	APP_ENV=test python scripts/chaos.py

lighthouse: stack-test
	APP_ENV=test $(E2E) node lighthouse-check.mjs

perf:
	docker compose exec api-1 python -m app.seed_orders 100000
	python scripts/perf_admin_orders.py
