#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"

if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then
    echo "research-loop needs Python 3.12 or newer; set PYTHON=/path/to/python3.12" >&2
    exit 1
fi

"$PYTHON" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[all]'

# Repair the links in .agents/skills and .claude/skills, which point at agent skills bundled
# inside installed packages. Setup still succeeds without uvx or if the repair fails.
if command -v uvx >/dev/null 2>&1; then
    VIRTUAL_ENV="$PWD/.venv" uvx library-skills --claude --yes >/dev/null \
        || echo "Could not repair agent skill links; run 'make skills' to retry." >&2
else
    echo "uvx not found; skipping agent skill links (see docs/setup.md#coding-agents)." >&2
fi

echo
echo "Bootstrap complete."
echo "Activate with: source .venv/bin/activate"
echo "Then run: pytest -q"
