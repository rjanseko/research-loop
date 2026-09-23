.PHONY: setup test graph diagnose postgres-up postgres-down db-status migrate

setup:
	./scripts/bootstrap.sh

test:
	. .venv/bin/activate && pytest -q

graph:
	. .venv/bin/activate && research-graph

diagnose:
	. .venv/bin/activate && research-diagnose

db-status:
	. .venv/bin/activate && research-db status

postgres-up:
	docker compose up -d postgres

postgres-down:
	docker compose down

migrate:
	. .venv/bin/activate && research-db migrate
