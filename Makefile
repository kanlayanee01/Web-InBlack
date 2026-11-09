TESTER = docker compose --profile test run --rm --build tester

.PHONY: up down stack-test lint test test-unit test-integration test-api

up:
	docker compose up --build

down:
	docker compose --profile test down -v

# API tests wipe the database, and /_test/reset only exists when APP_ENV=test.
stack-test:
	APP_ENV=test docker compose up --build -d --wait

lint:
	$(TESTER) sh -c "ruff check app tests alembic && mypy"

test-unit:
	$(TESTER) pytest tests/unit -q

test-integration:
	$(TESTER) pytest tests/integration -q

test-api: stack-test
	APP_ENV=test $(TESTER) pytest tests/api -q

test: stack-test lint test-unit test-integration test-api
