#!/usr/bin/env bash
# Исполняемая приёмка TestPilot 1C:	PASS/FAIL/SKIP по шагам, итоговая сводка и exit code.
# Использование: scripts/acceptance.sh [--cfe PATH] [--no-build]
# Переменные окружения: EXPECT_DEMO_ISSUES (по умолчанию 12), CFE_PATH, TP_EXPERT_PASSWORD, TP_EXPERT_USER.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
WEB=http://127.0.0.1:8000
IMPACT=http://127.0.0.1:8001
INTEL=http://127.0.0.1:8002
EXPECT_ISSUES="${EXPECT_DEMO_ISSUES:-12}"
CFE_FILE="${CFE_PATH:-}"
BUILD=1
while [ $# -gt 0 ]; do
  case "$1" in
    --cfe) CFE_FILE="${2:-}"; shift 2 ;;
    --no-build) BUILD=0; shift ;;
    *) echo "Неизвестный аргумент: $1"; exit 2 ;;
  esac
done

PASSED=0; FAILED=0; SKIPPED=0
pass() { PASSED=$((PASSED+1)); echo "PASS  $*"; }
fail() { FAILED=$((FAILED+1)); echo "FAIL  $*"; }
skip() { SKIPPED=$((SKIPPED+1)); echo "SKIP  $*"; }

USER_NAME="${TP_EXPERT_USER:-expert}"
read_env() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '\r'; }
PASSWORD="${TP_EXPERT_PASSWORD:-$(read_env TP_EXPERT_PASSWORD)}"
if [ -z "$PASSWORD" ]; then
  PASSWORD="$(docker compose exec -T intelligence printenv TP_EXPERT_PASSWORD 2>/dev/null | tr -d '\r')"
fi
if [ -z "$PASSWORD" ]; then
  echo "FAIL  не найден TP_EXPERT_PASSWORD ни в .env, ни в окружении контейнера"
  exit 1
fi
AUTH="$USER_NAME:$PASSWORD"

jget() { # jget <python-выражение над d>; JSON читается со stdin
  docker compose exec -T intelligence python -c "import json,sys
d=json.load(sys.stdin)
print(eval(sys.argv[1]))" "$1"
}
api() { # api <base> <method> <path> [json-body]
  if [ $# -ge 4 ]; then
    curl -s -u "$AUTH" -X "$2" -H 'Content-Type: application/json' -d "$4" "$1$3"
  else
    curl -s -u "$AUTH" -X "$2" "$1$3"
  fi
}
code_of() { curl -s -o /dev/null -w '%{http_code}' -u "$AUTH" "$1"; }

echo "=== 1. Стек ==="
if [ "$BUILD" = "1" ]; then
  if docker compose up -d --build --wait >/tmp/tp_acceptance_up.log 2>&1; then
    pass "docker compose up -d --build --wait"
  else
    fail "docker compose up -d --build --wait (см. /tmp/tp_acceptance_up.log)"
    echo "ИТОГО: passed=$PASSED failed=$FAILED skipped=$SKIPPED"; exit 1
  fi
else
  skip "docker compose up (--no-build)"
fi
HEALTH="$(api "$WEB" GET /health)"
STATUS="$(printf '%s' "$HEALTH" | jget "d.get('status')" 2>/dev/null)"
if [ "$STATUS" = "ok" ]; then pass "GET /health status=ok"; else fail "GET /health status=${STATUS:-нет ответа}"; fi
REVISION="$(docker compose exec -T intelligence alembic current 2>/dev/null | tr -d '\r' | tail -1)"
case "$REVISION" in
  *"0007 (head)"*) pass "alembic current: $REVISION" ;;
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
SCAN_BODY="$(docker compose exec -T -e TP_CODE="$CODE" intelligence python -c 'import json,os;print(json.dumps({"code":os.environ["TP_CODE"]}))')"
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
LLM_KEY="$(docker compose exec -T intelligence sh -c 'printf "%s" "${TP_LLM_API_KEY:-}"' 2>/dev/null | tr -d '\r')"
if [ -z "$LLM_KEY" ]; then
  skip "генерация плана: TP_LLM_API_KEY не задан"
elif [ -z "${PACKAGE:-}" ] || [ "$PACKAGE" = "None" ]; then
  fail "генерация плана: нет package_id из анализа ZIP"
else
  SUITE="$(api "$INTEL" POST /api/v1/ai/suites "{\"package_id\":\"$PACKAGE\",\"project_id\":\"$PROJECT\",\"provider\":\"llm\",\"consent\":true,\"object_names\":{},\"model_context\":[]}")"
  if printf '%s' "$SUITE" | grep -q 'upstream_status'; then
    skip "генерация плана недоступна (провайдер LLM): $(printf '%s' "$SUITE" | head -c 160)"
    SUITE=""
  elif printf '%s' "$SUITE" | grep -q '"error"'; then
    fail "генерация плана: $(printf '%s' "$SUITE" | head -c 200)"
    SUITE=""
  fi
  SUITE_ID="$(printf '%s' "${SUITE:-}" | jget "d.get('suite_id') or ''" 2>/dev/null)"
  INDEX="$(printf '%s' "$SUITE" | jget "$(cat <<'PY'
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
candidates = [i for i, p in enumerate(d.get('suggestion', {}).get('proposals') or []) if clean(p)]
print(candidates[0] if candidates else '')
PY
)" 2>/dev/null)"
  if [ -z "${SUITE:-}" ]; then
    : # причина уже напечатана выше
  elif [ -z "${SUITE_ID:-}" ] || [ "$SUITE_ID" = "None" ]; then
    fail "генерация плана llm: ${SUITE:0:200}"
  elif [ -z "${INDEX:-}" ] || [ "$INDEX" = "None" ]; then
    fail "в плане $SUITE_ID нет неразрушающего playwright-предложения"
  else
    pass "план llm: suite_id=$SUITE_ID, предложение $INDEX (навигация)"
    APPROVED="$(api "$INTEL" POST "/api/v1/ai/suites/$SUITE_ID/approve" "{\"reason\":\"acceptance\",\"selected_proposals\":[$INDEX]}")"
    APPROVED_STATUS="$(printf '%s' "$APPROVED" | jget "d.get('status')" 2>/dev/null)"
    if [ "$APPROVED_STATUS" = "approved" ]; then pass "approve: approved"; else fail "approve: ${APPROVED_STATUS:-нет ответа}"; fi
    RUN="$(api "$INTEL" POST "/api/v1/ai/suites/$SUITE_ID/run-1c")"
    PASSED_N="$(printf '%s' "$RUN" | jget "(d.get('counts') or {}).get('passed', 0)" 2>/dev/null)"
    EVIDENCE="$(printf '%s' "$RUN" | jget "next((r.get('evidence_id') for r in d.get('results') or [] if r.get('evidence_id')), '')" 2>/dev/null)"
    if [ "${PASSED_N:-0}" -ge 1 ] 2>/dev/null; then pass "прогон: passed=$PASSED_N"; else fail "прогон: passed=${PASSED_N:-0} ($(printf '%s' "$RUN" | head -c 160))"; fi
    if [ -n "${EVIDENCE:-}" ]; then pass "evidence: $EVIDENCE"; else fail "evidence отсутствует"; fi
    REPORT_CODE="$(code_of "$INTEL/api/v1/ai/suites/$SUITE_ID/report")"
    if [ "$REPORT_CODE" = "200" ]; then pass "отчёт о прогоне md: 200"; else fail "отчёт о прогоне md: $REPORT_CODE"; fi
  fi
fi

echo "=== 6. Тесты ==="
PYTEST_OUT="$(docker compose exec -T intelligence python -m pytest tests/ -q 2>&1)"
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
