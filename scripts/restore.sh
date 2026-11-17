#!/usr/bin/env bash
# Восстановление БД TestPilot из дампа.
# Пишет только в БД запущенного compose-стека: тома не удаляются, `down -v` не вызывается.
# Формат выбирается по расширению: .sql -> psql, .dump/.backup -> pg_restore (без --clean).
# Использование: bash scripts/restore.sh --dump PATH [--yes] [--force]
#   --yes   подтверждение: без него скрипт только печатает план и выходит с кодом 1
#   --force разрешает заливку в непустую БД (иначе отказ при наличии проектов)
# Переменные: POSTGRES_USER/POSTGRES_DB (иначе из контейнера db), COMPOSE_PROJECT_NAME.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DUMP=""
CONFIRMED=0
FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dump) DUMP="${2:-}"; shift 2 ;;
    --dump=*) DUMP="${1#*=}"; shift ;;
    --yes) CONFIRMED=1; shift ;;
    --force) FORCE=1; shift ;;
    *) echo "Неизвестный аргумент: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$DUMP" ]; then
  echo "Отказ: укажите дамп — bash scripts/restore.sh --dump путь/к/файлу.sql [--yes] [--force]" >&2
  exit 2
fi
if [ ! -f "$DUMP" ]; then echo "Отказ: файл дампа не найден: $DUMP" >&2; exit 2; fi
SIZE="$(wc -c < "$DUMP" | tr -d ' ')"
if [ "${SIZE:-0}" -le 0 ]; then echo "Отказ: дамп пуст: $DUMP" >&2; exit 2; fi

case "$DUMP" in
  *.sql) METHOD="psql" ;;
  *.dump|*.backup) METHOD="pg_restore" ;;
  *) echo "Отказ: неизвестное расширение (ожидается .sql, .dump или .backup): $DUMP" >&2; exit 2 ;;
esac

DB_USER="${POSTGRES_USER:-$(docker compose exec -T db printenv POSTGRES_USER 2>/dev/null | tr -d '\r')}"
DB_NAME="${POSTGRES_DB:-$(docker compose exec -T db printenv POSTGRES_DB 2>/dev/null | tr -d '\r')}"
DB_USER="${DB_USER:-testpilot}"
DB_NAME="${DB_NAME:-testpilot}"
PROJECT="${COMPOSE_PROJECT_NAME:-testpilot}"

PROJECTS="$(docker compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" -t -A -c 'SELECT count(*) FROM impact_projects' 2>/dev/null | tr -d '\r' || true)"
if ! printf '%s' "${PROJECTS:-}" | grep -Eq '^[0-9]+$'; then
  echo "Отказ: не удалось прочитать БД (стек не запущен или нет миграций). Запустите: docker compose -p $PROJECT up -d --wait" >&2
  exit 2
fi

echo "План восстановления:"
echo "  дамп: $DUMP ($SIZE байт, способ: $METHOD)"
echo "  проект compose: $PROJECT; база: $DB_USER@db/$DB_NAME"
echo "  текущих проектов в БД: $PROJECTS"
echo "  будут изменены только данные БД; тома, контейнеры и файлы не удаляются"

if [ "$CONFIRMED" -ne 1 ]; then
  echo "Отказ: повторите с --yes, чтобы выполнить восстановление." >&2
  exit 1
fi
if [ "$PROJECTS" -gt 0 ] && [ "$FORCE" -ne 1 ]; then
  echo "Отказ: в БД уже $PROJECTS проектов; заливка поверх живых данных требует --force." >&2
  exit 1
fi

if [ "$METHOD" = "psql" ]; then
  docker compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" < "$DUMP"
else
  docker compose exec -T db pg_restore -U "$DB_USER" -d "$DB_NAME" < "$DUMP"
fi

echo "Счётчики после восстановления:"
docker compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" -t -A \
  -c "SELECT 'impact_projects='||count(*) FROM impact_projects" \
  -c "SELECT 'impact_uploads='||count(*) FROM impact_uploads" \
  -c "SELECT 'ti_ai_suites='||count(*) FROM ti_ai_suites" \
  -c "SELECT 'code_audit='||count(*) FROM code_audit"
echo "Готово. Перезапустите сервисы: docker compose -p $PROJECT up -d --wait"
