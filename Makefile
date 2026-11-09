TESTER = docker compose --profile test run --rm --build tester

.PHONY: up down lint test test-unit test-integration

up:
	docker compose up --build

down:
	docker compose down -v

lint:
	$(TESTER) sh -c "ruff check app tests && mypy"

test-unit:
	$(TESTER) pytest tests/unit -q

test-integration:
	$(TESTER) pytest tests/integration -q

test: lint test-unit test-integration
