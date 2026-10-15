#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
set -a
source .env
set +a
mkdir -p .runtime
# Только процессы, созданные этим скриптом. Второй запуск не порождает дубликаты.
for spec in 'impact:8001' 'intelligence:8002' 'web:8000'; do
  service=${spec%:*}; port=${spec#*:}; pidfile=".runtime/${service}.pid"
  if [[ -f "$pidfile" ]] && .venv/bin/python - "$pidfile" "$service" <<'PYVERIFY'
import sys
from pathlib import Path
try:
 pid=int(Path(sys.argv[1]).read_text());cmd=Path(f'/proc/{pid}/cmdline').read_bytes()
 assert ('testpilot.'+sys.argv[2]+'.api:app').encode() in cmd
except (OSError,ValueError,AssertionError):sys.exit(1)
PYVERIFY
  then continue; fi
  nohup .venv/bin/uvicorn "testpilot.${service}.api:app" --host 127.0.0.1 --port "$port" > ".runtime/${service}.log" 2>&1 &
  echo "$!" > "$pidfile"
done
.venv/bin/python scripts/smoke.py --health-only
