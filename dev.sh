#!/usr/bin/env bash
# Developer shortcuts. Run inside Linux (WSL) from the repository root.
#
#   ./dev.sh setup         create the virtualenv and install labnet into it
#   ./dev.sh test          unit tests (fast, no Incus needed)
#   ./dev.sh integration   integration tests (creates and deletes real containers)
#   ./dev.sh lint          formatter check and linter
#   ./dev.sh format        reformat the code
#
# The virtualenv lives in your home directory rather than in the repository,
# because a checkout on the Windows side of WSL (/mnt/c/...) is slow for the
# thousands of small files a virtualenv contains.
set -euo pipefail
cd "$(dirname "$0")"
VENV="${LABNET_VENV:-$HOME/.venvs/labnet-dev}"

case "${1:-}" in
  setup)
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -e ".[dev,web]"
    echo "Ready. Run labnet with: $VENV/bin/labnet"
    ;;
  test)
    shift
    "$VENV/bin/pytest" -p no:cacheprovider "$@"
    ;;
  integration)
    shift
    "$VENV/bin/pytest" -p no:cacheprovider -m integration "$@"
    ;;
  lint)
    "$VENV/bin/ruff" format --check .
    "$VENV/bin/ruff" check .
    ;;
  format)
    "$VENV/bin/ruff" format .
    "$VENV/bin/ruff" check --fix .
    ;;
  *)
    sed -n '2,9p' "$0"
    exit 1
    ;;
esac
