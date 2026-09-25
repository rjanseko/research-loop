.PHONY: setup skills lock test lint doctor postgres-up postgres-down db-status migrate

VENV := .venv/bin

setup:
	./scripts/bootstrap.sh

# Repair the agent skill links after upgrading a package that bundles skills.
skills:
	VIRTUAL_ENV=$(CURDIR)/.venv uvx library-skills --claude --yes

# Pin every dependency of every extra, for all platforms. Existing pins are kept unless
# pyproject.toml needs a change; `make lock UPGRADE=1` moves everything to the newest allowed.
lock:
	uvx --from uv==0.8.17 uv pip compile pyproject.toml --extra all --universal --python-version 3.12 \
		-q -o requirements.lock $(if $(UPGRADE),--upgrade)

test:
	$(VENV)/pytest -q

lint:
	$(VENV)/ruff check .

doctor:
	$(VENV)/research doctor

db-status:
	$(VENV)/research db status

# --wait blocks until the healthcheck passes, so `make postgres-up migrate` works.
postgres-up:
	docker compose up -d --wait postgres

postgres-down:
	docker compose down

migrate:
	$(VENV)/research db migrate
