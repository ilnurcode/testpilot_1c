#!/usr/bin/env bash
# Офлайн-проверка: БД и сеть не нужны. Docker/compose/psql не вызываются.
# Запускает только тесты, которым хватает локального venv и файловой системы.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

PYTHON_BIN="${PYTHON:-}"
if [ -z "$PYTHON_BIN" ]; then
  if [ -x .venv/Scripts/python.exe ]; then
    PYTHON_BIN=.venv/Scripts/python.exe
  elif [ -x .venv/bin/python ]; then
    PYTHON_BIN=.venv/bin/python
  else
    echo "Не найден локальный venv (.venv/Scripts/python.exe или .venv/bin/python)." >&2
    echo "Создайте его: python -m venv .venv && .venv/bin/python -m pip install -e ." >&2
    exit 2
  fi
fi

FILES=(
  tests/test_1c_odata.py
  tests/test_1c_probe.py
  tests/test_api_docs_complete.py
  tests/test_binary_adapter.py
  tests/test_browser_runtime.py
  tests/test_compose_guard.py
  tests/test_docs_links.py
  tests/test_env_example_complete.py
  tests/test_gateway_forwarding.py
  tests/test_gateway_methods.py
  tests/test_numeric_env.py
  tests/test_open_url_security.py
  tests/test_openapi_access.py
  tests/test_openapi_operation_ids.py
  tests/test_optimizer.py
  tests/test_pytest_addopts.py
  tests/test_scripts_syntax.py
  tests/test_ui_smoke.py
  tests/test_xml_adapter.py
)

echo "Офлайн-проверка: БД и сеть не нужны (файлов: ${#FILES[@]})"
"$PYTHON_BIN" -m pytest "${FILES[@]}"
