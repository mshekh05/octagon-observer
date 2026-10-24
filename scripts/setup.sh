#!/usr/bin/env bash
# One-command setup: virtualenv, install, tests, then a full health check.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PYTHON:-python3}
"$PY" -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11+ required"'

[ -d .venv ] || "$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -e '.[dev]'

if [ ! -f .env ]; then
  cp .env.example .env
  echo ">> Created .env - open it and add ODDS_API_KEY plus NTFY_TOPIC or Telegram details, then re-run."
fi

python -m pytest -q
octagon-edge doctor "$@" || true
echo
echo "Next: octagon-edge report   (then open reports/ufc-333.html)"
echo "      octagon-edge watch --targets examples/targets.json   (leave running during the card)"
