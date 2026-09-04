#!/usr/bin/env bash
#
# Thin shim so `./query.sh` stays the entrypoint. The logic lives in query.py.
#
# uv resolves and syncs the environment from pyproject.toml / uv.lock on every
# invocation, so there is no venv to activate and no dependency drift.
#
# Every argument is passed straight through.
#
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

command -v uv >/dev/null || {
  echo "error: uv is not installed -- see https://docs.astral.sh/uv/" >&2
  exit 1
}

exec uv run -- python query.py "$@"
