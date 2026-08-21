#!/bin/sh
set -eu

if [ "$(id -u)" = "0" ]; then
  mkdir -p /app/logs /app/run /app/data/telegram_sessions
  chown -R transport:transport /app/logs /app/run /app/data
  exec gosu transport "$@"
fi

exec "$@"
