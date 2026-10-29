#!/usr/bin/env bash
# Prospecta MEI - abre o sistema no Linux ou macOS.
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Preparando o sistema pela primeira vez..."
  python3 -m venv .venv
  .venv/bin/pip install --disable-pip-version-check -q -r requirements.txt
fi
exec .venv/bin/python app.py "$@"
