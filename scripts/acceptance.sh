#!/usr/bin/env bash
# Исполняемая приёмка TestPilot 1C: PASS/FAIL/SKIP по шагам, итоговая сводка и exit code.
# ВНИМАНИЕ: не выполнять `docker compose down -v` на стенде с данными — это уничтожает БД и evidence.
# Для чистого старта используйте отдельный compose-проект (`-p testpilot-ci`) или CI-раннер.
# Использование: scripts/acceptance.sh [--cfe PATH] [--no-build] [--llm] [--live-1c] [--keep] [--probe-login]
# --llm: план через внешнего провайдера (нужен TP_LLM_API_KEY), при пустом ключе — FAIL, а не SKIP.
# --live-1c: approve навигационного предложения и run-1c на 1С; без --llm план строится provider=local.
# Флаги независимы: --live-1c работает без ключа LLM, --llm без --live-1c только создаёт и утверждает план.
# --probe-login: read-only зонд формы входа 1С для проекта из TP_ACCEPTANCE_PROJECT_ID (без ввода и кликов).
# --help: эта справка.
# Переменные окружения: EXPECT_DEMO_ISSUES (по умолчанию 12), CFE_PATH, TP_EXPERT_PASSWORD, TP_EXPERT_USER.
# Обязательная идентичность цели: TP_ACCEPTANCE_PROJECT (compose project) и TP_ACCEPTANCE_EXPECT_DB.
# При TP_ACCEPTANCE_DISPOSABLE=1 пароль берётся только из окружения (без чтения рабочего .env).
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
COMPOSE_FILE="${TP_ACCEPTANCE_COMPOSE_FILE:-$ROOT/compose.yaml}"
ENV_FILE="${TP_ACCEPTANCE_ENV_FILE:-}"
dc() {
  local extra=()
  if [ -n "$ENV_FILE" ]; then extra+=(--env-file "$ENV_FILE"); fi
  "$ROOT/scripts/compose.sh" -f "$COMPOSE_FILE" "${extra[@]}" "$@"
}
WEB=""
IMPACT=""
INTEL=""
EXPECT_ISSUES="${EXPECT_DEMO_ISSUES:-12}"
CFE_FILE="${CFE_PATH:-}"
BUILD=1
INCLUDE_LLM=0
INCLUDE_LIVE_1C=0
KEEP=0
PROBE_LOGIN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --cfe) CFE_FILE="${2:-}"; shift 2 ;;
    --no-build) BUILD=0; shift ;;
    --llm) INCLUDE_LLM=1; shift ;;
    --live-1c) INCLUDE_LIVE_1C=1; shift ;;
    --keep) KEEP=1; shift ;;
    --probe-login) PROBE_LOGIN=1; shift ;;
    --help|-h)
      sed -n '5,10p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "Неизвестный аргумент: $1"; exit 2 ;;
  esac
done

PASSED=0; FAILED=0; SKIPPED=0
pass() { PASSED=$((PASSED+1)); echo "PASS  $*"; }
fail() { FAILED=$((FAILED+1)); echo "FAIL  $*"; }
skip() { SKIPPED=$((SKIPPED+1)); echo "SKIP  $*"; }

PYTHON_BIN="${TP_PYTHON:-}"
if [ -z "$PYTHON_BIN" ]; then
  if [ -x "$ROOT/.venv/Scripts/python.exe" ]; then PYTHON_BIN="$ROOT/.venv/Scripts/python.exe"; else PYTHON_BIN="python3"; fi
fi
STACK_PROJECT="${TP_ACCEPTANCE_PROJECT:-}"
EXPECT_DB="${TP_ACCEPTANCE_EXPECT_DB:-}"
if [ -z "$STACK_PROJECT" ] || [ -z "$EXPECT_DB" ]; then
  echo "FAIL  target identity: задайте TP_ACCEPTANCE_PROJECT и TP_ACCEPTANCE_EXPECT_DB (без fallback на рабочий стек)" >&2
  exit 2
fi
IDENTITY_ARGS=(--project "$STACK_PROJECT" --expect-db "$EXPECT_DB" --compose-file "$COMPOSE_FILE")
if [ -n "$ENV_FILE" ]; then IDENTITY_ARGS+=(--env-file "$ENV_FILE"); fi
IDENTITY="$("$PYTHON_BIN" "$ROOT/scripts/target_identity.py" "${IDENTITY_ARGS[@]}")" || {
  echo "FAIL  target identity: стенд $STACK_PROJECT не подтверждён; записи не выполнялись" >&2
  exit 2
}
while IFS='=' read -r key value; do
  case "$key" in
    WEB) WEB="$value" ;;
    IMPACT) IMPACT="$value" ;;
    INTEL) INTEL="$value" ;;
  esac
done <<< "$IDENTITY"
if [ -z "$WEB" ] || [ -z "$IMPACT" ] || [ -z "$INTEL" ]; then
  echo "FAIL  target identity: не получены адреса стенда $STACK_PROJECT" >&2
  exit 2
fi
export COMPOSE_PROJECT_NAME="$STACK_PROJECT"

USER_NAME="${TP_EXPERT_USER:-expert}"
read_env() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r'; }
PASSWORD="${TP_EXPERT_PASSWORD:-}"
if [ -z "$PASSWORD" ] && [ "${TP_ACCEPTANCE_DISPOSABLE:-0}" != "1" ]; then
  PASSWORD="$(read_env TP_EXPERT_PASSWORD)"
fi
if [ -z "$PASSWORD" ] && [ "${TP_ACCEPTANCE_DISPOSABLE:-0}" != "1" ]; then
  PASSWORD="$(dc exec -T intelligence printenv TP_EXPERT_PASSWORD 2>/dev/null | tr -d '\r')"
fi
if [ -z "$PASSWORD" ]; then
  echo "FAIL  не найден TP_EXPERT_PASSWORD (для disposable-стенда пароль передаётся только окружением)"
  exit 1
fi
AUTH="$USER_NAME:$PASSWORD"

jget() { # jget <python-выражение над d>; JSON читается со stdin
  dc exec -T intelligence python -c "import json,sys
d=json.load(sys.stdin)
print(eval(sys.argv[1]))" "$1"
}
INSTANCE_TOKEN="${TP_ACCEPTANCE_INSTANCE_TOKEN:-}"
if [ -z "$INSTANCE_TOKEN" ]; then
  echo "FAIL  target identity: задайте TP_ACCEPTANCE_INSTANCE_TOKEN (positive marker одноразовой БД)" >&2
  exit 2
fi
for pair in "web:$WEB" "impact:$IMPACT" "intelligence:$INTEL"; do
  name="${pair%%:*}"; base="${pair#*:}"
  body="$(curl -s "$base/health")"
  actual_db="$(printf '%s' "$body" | jget "d.get('instance',{}).get('database') or ''")"
  marker_ok="$(printf '%s' "$body" | jget "bool(d.get('instance',{}).get('marker_ok'))")"
  if [ "$actual_db" != "$EXPECT_DB" ] || [ "$marker_ok" != "True" ]; then
    echo "FAIL  target identity: $name сообщает database=$actual_db marker_ok=$marker_ok, ожидалось $EXPECT_DB/True; записи не выполнялись" >&2
    exit 2
  fi
done
api() { # api <base> <method> <path> [json-body]
  if [ $# -ge 4 ]; then
    curl -s -u "$AUTH" -X "$2" -H 'Content-Type: application/json' -d "$4" "$1$3"
  else
    curl -s -u "$AUTH" -X "$2" "$1$3"
  fi
}
code_of() { curl -s -o /dev/null -w '%{http_code}' -u "$AUTH" "$1"; }

PROJECT=""
SCAN_ID=""
journal_count() {
  dc exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -t -A -c "select count(*) from code_audit"' 2>/dev/null | tr -d '\r' || true
}
cleanup() {
  local rc=$?
  if [ -n "${PROJECT:-}" ]; then
    if [ "$KEEP" = "1" ]; then
      echo "kept project ${PROJECT:0:8} (--keep)"
    else
      local code
      code="$(curl -s -o /dev/null -w '%{http_code}' -u "$AUTH" -X DELETE "$IMPACT/api/v1/projects/$PROJECT?confirm=$PROJECT")"
      if [ "$code" = "200" ]; then echo "deleted project ${PROJECT:0:8}"; else echo "FAIL  удаление проекта ${PROJECT:0:8}: HTTP $code"; fi
    fi
  fi
  if [ -n "${SCAN_ID:-}" ]; then
    if [ "$KEEP" = "1" ]; then
      echo "kept scan ${SCAN_ID:0:8} (--keep)"
    else
      local scan_code
      scan_code="$(curl -s -o /dev/null -w '%{http_code}' -u "$AUTH" -X DELETE "$WEB/api/v1/audit/scans/$SCAN_ID?confirm=$SCAN_ID")"
      if [ "$scan_code" = "200" ]; then echo "deleted scan ${SCAN_ID:0:8}"; else echo "FAIL  удаление скана ${SCAN_ID:0:8}: HTTP $scan_code"; fi
    fi
  fi
  echo "журнальных записей аудита кода: $(journal_count)"
  exit "$rc"
}
trap cleanup EXIT

echo "=== 1. Стек ==="
echo "ПРЕДУПРЕЖДЕНИЕ: не выполнять 'docker compose down -v' на стенде с данными — это уничтожает БД и evidence. Для чистого старта: отдельный проект (-p testpilot-ci) или CI-раннер."
if [ "$BUILD" = "1" ]; then
  if dc up -d --build --wait >/tmp/tp_acceptance_up.log 2>&1; then
    pass "dc up -d --build --wait"
  else
    fail "dc up -d --build --wait (см. /tmp/tp_acceptance_up.log)"
    echo "ИТОГО: passed=$PASSED failed=$FAILED skipped=$SKIPPED"; exit 1
  fi
else
  skip "dc up (--no-build)"
fi
HEALTH="$(api "$WEB" GET /health)"
STATUS="$(printf '%s' "$HEALTH" | jget "d.get('status')" 2>/dev/null)"
if [ "$STATUS" = "ok" ]; then pass "GET /health status=ok"; else fail "GET /health status=${STATUS:-нет ответа}"; fi
REVISION="$(dc exec -T intelligence alembic current 2>/dev/null | tr -d '\r' | tail -1)"
# Не сверяем с конкретной ревизией: любая голова допустима, важно, что схема на актуальной миграции.
case "$REVISION" in
  *"(head)"*) pass "alembic current: $REVISION" ;;
  *) fail "alembic current: ${REVISION:-нет ответа}" ;;
esac

echo "=== 2. Аудит демо-ZIP ==="
PROJECT="$(api "$IMPACT" POST /api/v1/projects "{\"name\":\"Acceptance $(date +%Y%m%d-%H%M%S)\",\"description\":\"acceptance.sh\"}" | jget "d['id']" 2>/dev/null)"
if [ -z "${PROJECT:-}" ]; then
  fail "создание проекта (нет id)"
else
  pass "проект создан: $PROJECT"
  UPLOAD="$(curl -s -u "$AUTH" -X POST --data-binary @samples/demo_vulnerable_extension.zip \
      -H 'Content-Type: application/octet-stream' \
      "$IMPACT/api/v1/projects/$PROJECT/uploads?filename=demo_vulnerable_extension.zip" | jget "d['id']" 2>/dev/null)"
  if [ -z "${UPLOAD:-}" ]; then fail "загрузка демо-ZIP"; else pass "загружен demo_vulnerable_extension.zip: $UPLOAD"; fi
  AUDIT="$(api "$WEB" POST /api/v1/audit/scan-upload "{\"project_id\":\"$PROJECT\",\"upload_id\":\"$UPLOAD\"}")"
  ISSUES="$(printf '%s' "$AUDIT" | jget "d.get('total_issues')" 2>/dev/null)"
  FILES="$(printf '%s' "$AUDIT" | jget "d.get('total_files')" 2>/dev/null)"
  SEVERITY="$(printf '%s' "$AUDIT" | jget "{k: d.get('issues_by_severity', {}).get(k, 0) for k in ('CRITICAL','HIGH','MEDIUM','LOW')}" 2>/dev/null)"
  if [ "$ISSUES" = "$EXPECT_ISSUES" ]; then pass "аудит: total_issues=$ISSUES"; else fail "аудит: total_issues=$ISSUES (ожидалось $EXPECT_ISSUES)"; fi
  if [ "$SEVERITY" = "{'CRITICAL': 1, 'HIGH': 7, 'MEDIUM': 1, 'LOW': 3}" ]; then pass "распределение 1/7/1/3"; else fail "распределение: $SEVERITY"; fi
  if [ "$FILES" = "8" ]; then pass "проверено файлов: 8"; else fail "проверено файлов: $FILES (ожидалось 8)"; fi
  ANALYSIS="$(api "$IMPACT" POST "/api/v1/projects/$PROJECT/uploads/$UPLOAD/analyze" '{}')"
  ANALYSIS_ID="$(printf '%s' "$ANALYSIS" | jget "d.get('analysis_id')" 2>/dev/null)"
  AFFECTED="$(printf '%s' "$ANALYSIS" | jget "len((d.get('package') or {}).get('affected_objects') or [])" 2>/dev/null)"
  DELIVERED="$(api "$IMPACT" POST "/api/v1/analyses/$ANALYSIS_ID/deliver")"
  PACKAGE="$(printf '%s' "$DELIVERED" | jget "d.get('package_id')" 2>/dev/null)"
  if [ "$AFFECTED" = "4" ]; then pass "analyze: затронуто объектов 4, package_id=$PACKAGE"; else fail "analyze: затронуто объектов $AFFECTED (ожидалось 4)"; fi
fi

echo "=== 3. Бинарный .cfe ==="
if [ -z "$CFE_FILE" ] || [ ! -f "$CFE_FILE" ]; then
  skip ".cfe: путь не задан или файл не найден (--cfe/CFE_PATH)"
else
  CFE_UPLOAD="$(curl -s -u "$AUTH" -X POST --data-binary @"$CFE_FILE" -H 'Content-Type: application/octet-stream' \
      "$IMPACT/api/v1/projects/$PROJECT/uploads?filename=$(basename "$CFE_FILE")")"
  CFE_ID="$(printf '%s' "$CFE_UPLOAD" | jget "d.get('id')" 2>/dev/null)"
  CFE_STATUS="$(printf '%s' "$CFE_UPLOAD" | jget "d.get('status')" 2>/dev/null)"
  if [ "$CFE_STATUS" = "metadata_only" ]; then pass ".cfe статус загрузки: metadata_only"; else fail ".cfe статус загрузки: $CFE_STATUS (ожидалось metadata_only)"; fi
  CFE_AUDIT="$(api "$WEB" POST /api/v1/audit/scan-upload "{\"project_id\":\"$PROJECT\",\"upload_id\":\"$CFE_ID\"}")"
  CFE_AUDIT_STATUS="$(printf '%s' "$CFE_AUDIT" | jget "d.get('status')" 2>/dev/null)"
  if [ "$CFE_AUDIT_STATUS" = "no_bsl_modules" ]; then pass ".cfe аудит: no_bsl_modules"; else fail ".cfe аудит: $CFE_AUDIT_STATUS (ожидалось no_bsl_modules)"; fi
fi

echo "=== 4. Журнал аудита кода ==="
CODE='Процедура Тест()
	ОтключитьБезопасныйРежим(Истина);
	Запрос.Текст = "ВЫБРАТЬ * ИЗ Справочник.Товары"
		+ Переменная;
	Объект = Новый COMОбъект("Excel.Application");
КонецПроцедуры
'
SCAN_BODY="$(dc exec -T -e TP_CODE="$CODE" intelligence python -c 'import json,os;print(json.dumps({"code":os.environ["TP_CODE"]}))')"
SCAN="$(api "$WEB" POST /api/v1/audit/scan "$SCAN_BODY")"
SCAN_ID="$(printf '%s' "$SCAN" | jget "d.get('scan_id')" 2>/dev/null)"
RULES="$(printf '%s' "$SCAN" | jget "sorted(i['rule_id'] for i in d.get('issues') or [])" 2>/dev/null)"
if [ "$RULES" = "['SEC010', 'SEC011', 'SEC012']" ]; then pass "скан трёх конструкций: SEC010/SEC011/SEC012, scan_id=$SCAN_ID"; else fail "скан трёх конструкций: $RULES"; fi
MD_CODE="$(curl -s -o /tmp/tp_acceptance.md -w '%{http_code}' -u "$AUTH" "$WEB/api/v1/audit/scans/$SCAN_ID/report")"
MD_SIZE="$(wc -c < /tmp/tp_acceptance.md | tr -d ' ')"
CSV_CODE="$(curl -s -o /tmp/tp_acceptance.csv -w '%{http_code}' -u "$AUTH" "$WEB/api/v1/audit/scans/$SCAN_ID/report?format=csv")"
CSV_SIZE="$(wc -c < /tmp/tp_acceptance.csv | tr -d ' ')"
if [ "$MD_CODE" = "200" ] && [ "$MD_SIZE" -gt 0 ]; then pass "отчёт md: 200, $MD_SIZE байт"; else fail "отчёт md: код $MD_CODE, $MD_SIZE байт"; fi
if [ "$CSV_CODE" = "200" ] && [ "$CSV_SIZE" -gt 0 ]; then pass "отчёт csv: 200, $CSV_SIZE байт"; else fail "отчёт csv: код $CSV_CODE, $CSV_SIZE байт"; fi

echo "=== 5. ИИ: план и прогон ==="
LLM_KEY="$(dc exec -T intelligence sh -c 'printf "%s" "${TP_LLM_API_KEY:-}"' 2>/dev/null | tr -d '\r')"
if [ "$INCLUDE_LLM" = "0" ] && [ "$INCLUDE_LIVE_1C" = "0" ]; then
  skip "генерация плана и прогон отключены: добавьте --llm (внешний провайдер) и/или --live-1c (прогон на 1С)"
else
  PROVIDER=local
  if [ "$INCLUDE_LLM" = "1" ]; then
    PROVIDER=llm
  fi
  SUITE=""
  PLAN_PROJECT="$PROJECT"
  LIVE_ERROR=""
  if [ "$INCLUDE_LIVE_1C" = "1" ]; then
    LIVE_PROJECT="${TP_ACCEPTANCE_PROJECT_ID:-}"
    if [ -z "$LIVE_PROJECT" ]; then
      fail "--live-1c требует TP_ACCEPTANCE_PROJECT_ID: id проекта с настроенным подключением (web_url/odata_url/пользователь)"
      LIVE_ERROR=1
    else
      LIVE_JSON="$(api "$IMPACT" GET "/api/v1/projects/$LIVE_PROJECT")"
      LIVE_WEB="$(printf '%s' "$LIVE_JSON" | jget "(d.get('connection_settings') or {}).get('web_url') or d.get('web_url') or ''" 2>/dev/null)"
      if [ -z "${LIVE_WEB:-}" ]; then
        fail "у проекта $LIVE_PROJECT не настроен web_url; заполните PUT /connection и повторите"
        LIVE_ERROR=1
      else
        PLAN_PROJECT="$LIVE_PROJECT"
      fi
    fi
  fi
  if [ -n "$LIVE_ERROR" ]; then
    :
  elif [ "$PROVIDER" = "llm" ] && [ -z "$LLM_KEY" ]; then
    fail "генерация плана llm: TP_LLM_API_KEY не задан"
  elif [ -z "${PACKAGE:-}" ] || [ "$PACKAGE" = "None" ]; then
    fail "генерация плана: нет package_id из анализа ZIP"
  elif [ "$PROVIDER" = "llm" ]; then
    SUITE="$(api "$INTEL" POST /api/v1/ai/suites "{\"package_id\":\"$PACKAGE\",\"project_id\":\"$PLAN_PROJECT\",\"provider\":\"llm\",\"consent\":true,\"object_names\":{},\"model_context\":[]}")"
    if printf '%s' "$SUITE" | grep -q 'upstream_status'; then
      fail "генерация плана llm: $(printf '%s' "$SUITE" | head -c 200)"
      SUITE=""
    elif printf '%s' "$SUITE" | grep -q '"error"'; then
      fail "генерация плана llm: $(printf '%s' "$SUITE" | head -c 200)"
      SUITE=""
    fi
  else
    SUITE="$(api "$INTEL" POST /api/v1/ai/suites "{\"package_id\":\"$PACKAGE\",\"project_id\":\"$PLAN_PROJECT\",\"provider\":\"local\"}")"
    if printf '%s' "$SUITE" | grep -q '"error"'; then
      fail "генерация плана local: $(printf '%s' "$SUITE" | head -c 200)"
      SUITE=""
    fi
  fi
  if [ -n "${SUITE:-}" ]; then
  SUITE_ID="$(printf '%s' "$SUITE" | jget "d.get('suite_id') or ''" 2>/dev/null)"
  # jget использует eval(), а eval не принимает def/присваивания: скрипт передаём как -c (exec-режим).
  INDEX="$(printf '%s' "$SUITE" | dc exec -T intelligence python -c "$(cat <<'PY'
import json, sys
markers = ('записать','провести','создать','удалить','отменить проведение','пометить на удаление','скопировать','изменить')
def clean(p):
    if p.get('engine') != 'playwright': return False
    for case in p.get('cases') or []:
        for step in case.get('steps') or []:
            action = (step.get('action') or '').lower()
            target = (step.get('target') or '').casefold()
            if action == 'fill': return False
            if action == 'click' and any(m in target for m in markers): return False
    return True
d = json.load(sys.stdin)
candidates = [i for i, p in enumerate(d.get('suggestion', {}).get('proposals') or []) if clean(p)]
print(candidates[0] if candidates else '')
PY
)" 2>/dev/null)"
  if [ -z "${SUITE_ID:-}" ] || [ "$SUITE_ID" = "None" ]; then
    fail "генерация плана $PROVIDER: ${SUITE:0:200}"
  elif [ -z "${INDEX:-}" ] || [ "$INDEX" = "None" ]; then
    fail "в плане $SUITE_ID нет неразрушающего playwright-предложения"
  else
    pass "план $PROVIDER: suite_id=$SUITE_ID, предложение $INDEX (навигация)"
    APPROVED="$(api "$INTEL" POST "/api/v1/ai/suites/$SUITE_ID/approve" "{\"reason\":\"acceptance\",\"selected_proposals\":[$INDEX]}")"
    APPROVED_STATUS="$(printf '%s' "$APPROVED" | jget "d.get('status')" 2>/dev/null)"
    if [ "$APPROVED_STATUS" = "approved" ]; then pass "approve: approved"; else fail "approve: ${APPROVED_STATUS:-нет ответа}"; fi
    if [ "$INCLUDE_LIVE_1C" = "0" ]; then
      skip "прогон на 1С отключён: добавьте --live-1c"
    else
    RUN_ACCEPTED="$(api "$INTEL" POST "/api/v1/ai/suites/$SUITE_ID/run-1c")"
    RUN_STATE="queued"
    for _ in $(seq 1 120); do
      RUN_STATUS_JSON="$(api "$INTEL" GET "/api/v1/ai/suites/$SUITE_ID/status")"
      RUN_STATE="$(printf '%s' "$RUN_STATUS_JSON" | jget "d.get('status')" 2>/dev/null)"
      [ "$RUN_STATE" = "queued" ] || [ "$RUN_STATE" = "running" ] || break
      sleep 2
    done
    if [ "$RUN_STATE" = "completed" ]; then pass "прогон: completed"; else fail "прогон: status=${RUN_STATE:-нет ответа}"; fi
    RUN="$(api "$INTEL" GET "/api/v1/ai/suites/$SUITE_ID")"
    PASSED_N="$(printf '%s' "$RUN" | jget "(d.get('counts') or {}).get('passed', 0)" 2>/dev/null)"
    EVIDENCE="$(printf '%s' "$RUN" | jget "next((r.get('evidence_id') for r in d.get('results') or [] if r.get('evidence_id')), '')" 2>/dev/null)"
    if [ -n "${PASSED_N:-}" ]; then
      if [ "${PASSED_N:-0}" -ge 1 ] 2>/dev/null; then pass "прогон: passed=$PASSED_N"; else fail "прогон: passed=${PASSED_N:-0} ($(printf '%s' "$RUN" | head -c 160))"; fi
      if [ -n "${EVIDENCE:-}" ]; then pass "evidence: $EVIDENCE"; else fail "evidence отсутствует"; fi
      REPORT_CODE="$(code_of "$INTEL/api/v1/ai/suites/$SUITE_ID/report")"
      if [ "$REPORT_CODE" = "200" ]; then pass "отчёт о прогоне md: 200"; else fail "отчёт о прогоне md: $REPORT_CODE"; fi
    fi
    fi
  fi
  fi
fi

echo "=== 5a. Зонд формы входа (read-only) ==="
if [ "$PROBE_LOGIN" -eq 0 ]; then
  skip "probe-login: флаг не передан"
else
  PROBE_PROJECT="${TP_ACCEPTANCE_PROJECT_ID:-}"
  if [ -z "$PROBE_PROJECT" ]; then
    fail "probe-login: укажите TP_ACCEPTANCE_PROJECT_ID — id проекта с настроенным web_url"
  else
    PROBE_RESP="$(api "$INTEL" POST /api/v1/ai/connection/probe-login "{\"project_id\":\"$PROBE_PROJECT\"}")"
    PROBE_FORM="$(printf '%s' "$PROBE_RESP" | jget "isinstance(d.get('login_form_visible'), bool)" 2>/dev/null)"
    if [ "$PROBE_FORM" = "True" ]; then
      pass "probe-login: $(printf '%s' "$PROBE_RESP" | jget "f\"login_form_visible={d['login_form_visible']}, fields={len(d.get('fields') or [])}, buttons={len(d.get('buttons') or [])}\"" 2>/dev/null)"
    else
      fail "probe-login: $(printf '%s' "$PROBE_RESP" | jget "d.get('error',{}).get('message') or str(d)[:300]" 2>/dev/null)"
    fi
  fi
fi

echo "=== 6. Тесты ==="
# UI smoke требует адрес интерфейса и пароль эксперта; без них тест сам пропускается.
# Внутри compose web виден как web:8000; для host-запуска задайте TP_UI_BASE_URL=http://127.0.0.1:8000.
export TP_UI_BASE_URL="${TP_UI_BASE_URL:-http://web:8000}"
export TP_EXPERT_PASSWORD="${TP_EXPERT_PASSWORD:-TestPilotLocal123!}"
# В приёмке интерфейс обязателен: недоступный UI — FAIL, а не SKIP.
export TP_UI_REQUIRED=1
UI_OUT="$(dc exec -T -e TP_UI_BASE_URL="$TP_UI_BASE_URL" -e TP_EXPERT_PASSWORD="$TP_EXPERT_PASSWORD" -e TP_UI_REQUIRED=1 intelligence python -m pytest tests/test_ui_smoke.py -q 2>&1)"
UI_RC=$?
UI_SKIPPED="$(printf '%s' "$UI_OUT" | grep -E '[0-9]+ skipped' | tail -1)"
UI_SUMMARY="$(printf '%s' "$UI_OUT" | grep -E '[0-9]+ passed' | tail -1)"
if [ "$UI_RC" = "0" ] && [ -n "$UI_SUMMARY" ]; then
  UI_COUNT="$(printf '%s' "$UI_SUMMARY" | sed -E 's/^([0-9]+) passed.*/\1/')"
  pass "UI smoke: $UI_COUNT проверок OK (overview, projects, project, audit, ai)"
elif [ "$UI_RC" = "0" ] && [ -n "$UI_SKIPPED" ]; then
  echo "SKIP  UI smoke: $UI_SKIPPED"
  SKIPPED=$((SKIPPED+1))
else
  FAILED=$((FAILED+1))
  echo "FAIL  UI smoke: $(printf '%s' "$UI_OUT" | grep -E 'раздел #|AssertionError' | head -3 | tr '\n' ' ')"
fi
PYTEST_OUT="$(dc exec -T -e TP_UI_BASE_URL="$TP_UI_BASE_URL" -e TP_EXPERT_PASSWORD="$TP_EXPERT_PASSWORD" -e TP_UI_REQUIRED=1 intelligence python -m pytest tests/ -q 2>&1)"
PYTEST_RC=$?
PYTEST_SUMMARY="$(printf '%s' "$PYTEST_OUT" | grep -E '[0-9]+ passed' | tail -1)"
if [ "$PYTEST_RC" = "0" ] && [ -n "$PYTEST_SUMMARY" ]; then pass "pytest: $PYTEST_SUMMARY"; else fail "pytest: код $PYTEST_RC, ${PYTEST_SUMMARY:-нет сводки}"; fi

echo "=== ИТОГО ==="
echo "passed=$PASSED failed=$FAILED skipped=$SKIPPED"
if [ "$FAILED" -gt 0 ]; then
  echo "РЕЗУЛЬТАТ: FAIL"
  exit 1
fi
echo "РЕЗУЛЬТАТ: PASS"
exit 0
