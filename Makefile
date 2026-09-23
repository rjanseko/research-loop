.PHONY: setup test graph diagnose postgres-up postgres-down db-status migrate

VENV := .venv/bin

setup:
	./scripts/bootstrap.sh

test:
	$(VENV)/pytest -q

graph:
	$(VENV)/research-graph

diagnose:
	$(VENV)/research-diagnose

db-status:
	$(VENV)/research-db status

# --wait blocks until the healthcheck passes, so `make postgres-up migrate` works.
postgres-up:
	docker compose up -d --wait postgres

postgres-down:
	docker compose down

migrate:
	$(VENV)/research-db migrate
