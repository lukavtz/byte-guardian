#!/bin/sh
# Container entrypoint: migrate first, then run the given command.
# Default command is the bot; compose overrides it with app.worker.
set -eu

echo "entrypoint: alembic upgrade head"
if ! uv run alembic upgrade head; then
    echo "entrypoint: alembic upgrade head failed, not starting" >&2
    exit 1
fi

exec "$@"