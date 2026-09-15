#!/bin/zsh
set -u

APP_DIR="${0:A:h}"
cd "$APP_DIR" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  printf '\nPython 3 is required but was not found.\n'
  printf 'Press Return to close.\n'
  read -r
  exit 1
fi

exec python3 "$APP_DIR/server.py" --open
