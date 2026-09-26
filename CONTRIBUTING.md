# Contributing

Pull requests are welcome. Fork the repository, make your change on a branch, and open a pull
request against `master`. A maintainer reviews and approves every pull request before it is
merged. For a large change, open an issue first so we can agree on the
approach before you write it.

## Setup

```bash
make setup   # creates .venv from requirements.lock (Python 3.12+)
make lint    # ruff
make test    # pytest, offline
```

The test suite needs no API keys or network access. `tests/conftest.py` blocks model-provider
requests and any non-loopback host. Script models with `FunctionModel`, and serve fetches with the
`serve` and `public_urls` fixtures. The Postgres tests are skipped unless `RESEARCH_TEST_DATABASE_URL` names
a database whose name contains `test`; see the top of `tests/test_store.py`.

CI runs lint, the full test suite against Postgres, a lockfile check, and a wheel install on every
pull request, including ones from forks. If you change dependencies in `pyproject.toml`, run
`make lock` and commit `requirements.lock`.

## Guidelines

[AGENTS.md](AGENTS.md) sets out the architecture boundaries, compatibility rules, and which
document to update with each kind of change. It applies to human contributors as well as coding
agents. The main points:

- Keep the `research` command and the `scout(...)` function in `scout.py` working as documented.
- Role instructions live in `prompts.py` and the agents in `agents.py`. Any change to text a
  model sees, including the scouts' budget notes, counts as a behavior change, so say so in the
  pull request.
- Update the matching document in `docs/` along with the code.
- Never commit `.env` files, API keys, or benchmark inputs.

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
