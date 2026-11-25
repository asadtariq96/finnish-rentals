#!/usr/bin/env bash
# Start the Vuokraovi live-search web app.
# Requires: ./appenv/bin/python (Flask). Create once with:
#   python3 -m venv appenv && ./appenv/bin/pip install flask
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-8933}"
exec ./appenv/bin/python app/app.py
