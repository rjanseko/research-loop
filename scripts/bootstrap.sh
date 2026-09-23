#!/usr/bin/env bash
set -euo pipefail

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e '.[all]'

echo
echo "Bootstrap complete."
echo "Activate with: source .venv/bin/activate"
echo "Then run: pytest -q"
