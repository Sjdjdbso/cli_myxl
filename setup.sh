#!/bin/sh
set -eu

PYTHON="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON" ]; then
    echo "Python 3 is required. Install Python 3 and pip with your system package manager." >&2
    exit 1
fi

if ! "$PYTHON" -m pip --version >/dev/null 2>&1; then
    echo "pip is required. Install it with your system package manager." >&2
    exit 1
fi

"$PYTHON" -m pip install -r requirements.txt
