.PHONY: setup test graph postgres-up postgres-down migrate

setup:
	./scripts/bootstrap.sh

test:
	. .venv/bin/activate && pytest -q

graph:
	. .venv/bin/activate && research-graph

postgres-up:
	docker compose up -d postgres

postgres-down:
	docker compose down

migrate:
	./scripts/db_migrate.sh
