#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_CACHE_DIR=/tmp/testpilot-uv
export DOCKER_CONFIG="${DOCKER_CONFIG:-/tmp/testpilot-docker}"
if [[ ! -x .venv/bin/python ]]; then uv venv .venv; fi
uv pip install --python .venv/bin/python -r requirements.lock
uv pip install --python .venv/bin/python --no-deps -e .
.venv/bin/python scripts/local_env.py
set -a
source .env
set +a
.venv/bin/python - <<'PY'
from testpilot.ai.executor import browser_path
if not browser_path():raise SystemExit('Не найден Chromium. Установите Chromium из доверенного источника и задайте TP_CHROMIUM_PATH.')
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
 b=p.chromium.launch(executable_path=browser_path(),headless=True,args=['--no-sandbox']);b.close()
print('Chromium и Playwright: готовы к проверке учебного стенда')
PY
docker compose up -d --wait db
.venv/bin/alembic upgrade head
# Порты приложений освобождаем только у контейнеров и процессов этого проекта.
docker compose stop impact intelligence web
.venv/bin/python - <<'PY'
import os,time
from pathlib import Path
for service in ('impact','intelligence','web'):
 f=Path('.runtime')/(service+'.pid')
 if not f.exists():continue
 try:
  pid=int(f.read_text());cmd=Path(f'/proc/{pid}/cmdline').read_bytes()
  if ('testpilot.'+service+'.api:app').encode() not in cmd:continue
  os.kill(pid,15)
  for _ in range(50):
   if not Path(f'/proc/{pid}').exists():break
   time.sleep(.1)
 except (OSError,ValueError):pass
PY
./scripts/start-local.sh
printf '%s\n' 'Презентационный режим готов. В интерфейсе: Обзор → Открыть демонстрацию.'
