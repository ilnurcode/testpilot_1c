#!/usr/bin/env bash
# Обёртка над `docker compose`: пробрасывает аргументы, но запрещает уничтожать тома рабочего проекта.
# Разрешено: `down -v` при CI=1 или с -p testpilot-ci / -p testpilot-restore; `down` без -v; обычные команды.
# Запрещено (код 3): `down -v` на рабочем проекте, `volume rm`/`volume prune`, задевающие testpilot.
set -uo pipefail

REFUSAL_DOWN='Отказано: `down -v` удаляет тома (БД и evidence). На рабочем стенде недопустимо. Используйте `-p testpilot-ci` или `-p testpilot-restore`, либо `docker compose down` без `-v`.'
REFUSAL_VOLUME='Отказано: удаление томов проекта testpilot уничтожает БД и evidence. Используйте `-p testpilot-ci` или `-p testpilot-restore`, либо CI=1.'
ALLOWED_PROJECTS='testpilot-ci testpilot-restore'

args=("$@")
is_ci=0
case "${CI:-}" in 1|true|yes) is_ci=1 ;; esac

project=""
has_down=0
has_volumes=0
volume_sub=""
volume_targets=()
i=0
while [ "$i" -lt "${#args[@]}" ]; do
  a="${args[$i]}"
  case "$a" in
    -p|--project) i=$((i+1)); project="${args[$i]:-}" ;;
    --project=*) project="${a#*=}" ;;
    -p*) project="${a#-p}" ;;
    down) has_down=1 ;;
    -v|--volumes) has_volumes=1 ;;
    volume)
      volume_sub="${args[$((i+1))]:-}"
      j=$((i+2))
      while [ "$j" -lt "${#args[@]}" ]; do volume_targets+=("${args[$j]}"); j=$((j+1)); done
      i="${#args[@]}"
      ;;
  esac
  i=$((i+1))
done

project_allowed=0
for allowed in $ALLOWED_PROJECTS; do
  [ "$project" = "$allowed" ] && project_allowed=1
done

if [ "$has_down" -eq 1 ] && [ "$has_volumes" -eq 1 ] && [ "$is_ci" -eq 0 ] && [ "$project_allowed" -eq 0 ]; then
  echo "$REFUSAL_DOWN" >&2
  exit 3
fi

if [ -n "$volume_sub" ] && [ "$is_ci" -eq 0 ]; then
  case "$volume_sub" in
    prune)
      echo "$REFUSAL_VOLUME" >&2
      exit 3
      ;;
    rm)
      for target in "${volume_targets[@]}"; do
        case "$target" in
          testpilot*) echo "$REFUSAL_VOLUME" >&2; exit 3 ;;
        esac
      done
      ;;
  esac
fi

docker compose "$@"
