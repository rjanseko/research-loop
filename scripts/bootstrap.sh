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

echo
echo "Bootstrap complete."
echo "Activate with: source .venv/bin/activate"
echo "Then run: pytest -q"
