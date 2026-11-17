#!/usr/bin/env bash
# ЧЕРНОВИК: не проверялся на реальном дампе; только отдельный стек (`-p testpilot-restore`), не рабочий;
# pg_restore с --exit-on-error.
#
# Восстановление БД TestPilot из дампа. Пишет только в БД указанного compose-проекта;
# тома не удаляются, `down -v` не вызывается.
# ТОЛЬКО В ПУСТУЮ СХЕМУ: плейн-дамп содержит CREATE TABLE, а `up -d --wait` запускает migrate
# и создаёт таблицы — тогда заливка падает. Правильный порядок:
#   1) docker compose -p testpilot-restore up -d db        # только база, без migrate
#   2) bash scripts/restore.sh --dump PATH --yes --project testpilot-restore
#   3) если в дампе есть alembic_version — docker compose -p testpilot-restore up -d --wait
#      если нет — сначала сверить таблицы и выполнить alembic stamp head вручную;
#      `up -d --wait` в этом случае упадёт (migrate попытается создать существующие таблицы).
# Формат по расширению: .sql -> psql -v ON_ERROR_STOP=1, .dump/.backup -> pg_restore --clean --if-exists --no-owner --exit-on-error.
# Использование: bash scripts/restore.sh --dump PATH --yes [--project NAME]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DUMP=""
CONFIRMED=0
PROJECT="${COMPOSE_PROJECT_NAME:-testpilot}"
while [ $# -gt 0 ]; do
  case "$1" in
    --dump) DUMP="${2:-}"; shift 2 ;;
    --dump=*) DUMP="${1#*=}"; shift ;;
    --yes) CONFIRMED=1; shift ;;
    --project) PROJECT="${2:-}"; shift 2 ;;
    --project=*) PROJECT="${1#*=}"; shift ;;
    --force|-Force) echo "Отказ: --force удалён. Восстановление возможно только в пустую схему." >&2; exit 2 ;;
    *) echo "Неизвестный аргумент: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$DUMP" ]; then
  echo "Отказ: укажите дамп — bash scripts/restore.sh --dump путь/к/файлу.sql --yes [--project NAME]" >&2
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

dc() { "$ROOT/scripts/compose.sh" -p "$PROJECT" "$@"; }
DB_USER="${POSTGRES_USER:-$(dc exec -T db printenv POSTGRES_USER 2>/dev/null | tr -d '\r')}"
DB_NAME="${POSTGRES_DB:-$(dc exec -T db printenv POSTGRES_DB 2>/dev/null | tr -d '\r')}"
DB_USER="${DB_USER:-testpilot}"
DB_NAME="${DB_NAME:-testpilot}"

TABLES="$(dc exec -T db psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" -t -A \
  -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'" 2>/dev/null | tr -d '\r' || true)"
if ! printf '%s' "${TABLES:-}" | grep -Eq '^[0-9]+$'; then
  echo "Отказ: не удалось прочитать БД проекта $PROJECT (контейнер db не запущен)." >&2
  echo "       Запустите только базу: docker compose -p $PROJECT up -d db, затем повторите restore." >&2
  exit 2
fi

if [ "$METHOD" = "psql" ]; then
  if grep -qE 'COPY (public\.)?alembic_version|INSERT INTO (public\.)?alembic_version' "$DUMP"; then HAS_ALEMBIC="да"; else HAS_ALEMBIC="нет"; fi
else
  if dc exec -T db pg_restore --exit-on-error --list < "$DUMP" 2>/dev/null | grep -q alembic_version; then HAS_ALEMBIC="да"; else HAS_ALEMBIC="нет"; fi
fi

echo "План восстановления:"
echo "  дамп: $DUMP ($SIZE байт, способ: $METHOD)"
echo "  проект compose: $PROJECT; база: $DB_USER@db/$DB_NAME"
echo "  схема public: $TABLES таблиц (нужна пустая)"
echo "  alembic_version в дампе: $HAS_ALEMBIC"
echo "  будут изменены только данные БД; тома, контейнеры и файлы не удаляются"

if [ "$TABLES" -gt 0 ]; then
  echo "Отказ: схема содержит $TABLES таблиц: восстановление возможно только в пустую БД. Запустите только базу: \`docker compose -p testpilot-restore up -d db\`, затем повторите restore; приложение поднимайте после восстановления." >&2
  exit 1
fi
if [ "$CONFIRMED" -ne 1 ]; then
  echo "Отказ: повторите с --yes, чтобы выполнить восстановление." >&2
  exit 1
fi

set +e
if [ "$METHOD" = "psql" ]; then
  dc exec -T db psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" < "$DUMP"
else
  dc exec -T db pg_restore --clean --if-exists --no-owner --exit-on-error -U "$DB_USER" -d "$DB_NAME" < "$DUMP"
fi
RESTORE_RC=$?
set -e
if [ "$RESTORE_RC" -ne 0 ]; then
  echo "FAIL  восстановление завершилось с кодом $RESTORE_RC" >&2
  exit 1
fi

echo "Счётчики после восстановления:"
dc exec -T db psql -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" -t -A \
  -c "SELECT 'impact_projects='||count(*) FROM impact_projects" \
  -c "SELECT 'impact_uploads='||count(*) FROM impact_uploads" \
  -c "SELECT 'ti_ai_suites='||count(*) FROM ti_ai_suites" \
  -c "SELECT 'code_audit='||count(*) FROM code_audit"
if [ "$HAS_ALEMBIC" = "да" ]; then
  echo "Готово. Поднимайте приложение: docker compose -p $PROJECT up -d --wait"
else
  echo "ВНИМАНИЕ: в дампе нет alembic_version. Сверьте таблицы и выполните alembic stamp head вручную:" >&2
  echo "  docker compose -p $PROJECT run --rm migrate alembic stamp head  (или exec в поднятом сервисе)" >&2
  echo "  'up -d --wait' до этого упадёт: migrate попытается создать уже существующие таблицы." >&2
fi
