#!/bin/sh
# Обёртка: запускает CLI из репозитория без установки пакета.
#   ./run.sh index --corpus examples/corpus --out build/index.json
set -e
cd "$(dirname "$0")"
PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}" exec python3 -m reportgen "$@"
