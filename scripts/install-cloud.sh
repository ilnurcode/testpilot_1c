#!/usr/bin/env bash
set -euo pipefail
# Подготовка браузерного исполнителя выполняется на хосте, PostgreSQL — в Compose.
exec "$(dirname "$0")/start-presentation.sh"
