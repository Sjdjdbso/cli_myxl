#!/bin/sh
set -eu

PYTHON="$(command -v python3 || command -v python || true)"
if [ -n "$PYTHON" ] && ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    PYTHON=""
fi

if [ -z "$PYTHON" ] || ! "$PYTHON" -m pip --version >/dev/null 2>&1; then
    if command -v opkg >/dev/null 2>&1; then
        opkg update
        opkg install python3 python3-pip
    elif command -v apk >/dev/null 2>&1; then
        apk add python3 py3-pip
    else
        echo "Python 3.10+ and pip are required. Install python3 and python3-pip with your system package manager." >&2
        exit 1
    fi
fi

PYTHON="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON" ] || ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "Python 3.10 or newer is required." >&2
    exit 1
fi

"$PYTHON" -m pip install -r requirements.txt
