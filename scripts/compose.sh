#!/usr/bin/env bash
# Обёртка над `docker compose`: пробрасывает аргументы, но запрещает уничтожать тома рабочего проекта.
# Проект: -p/--project, иначе COMPOSE_PROJECT_NAME, иначе testpilot.
# Разрешено: `down -v` и volume-операции только для проектов testpilot-ci / testpilot-restore; `down` без -v; остальные команды.
# Запрещено (код 3): `down -v` на рабочем проекте, `volume rm` с томами testpilot, `volume prune`.
# CI не снимает запрет: он влияет только на текст подсказки.
set -uo pipefail

ALLOWED_PROJECTS='testpilot-ci testpilot-restore'
project="${COMPOSE_PROJECT_NAME:-}"
has_down=0
has_volumes=0
volume_sub=""
volume_targets=()
i=0
# shellcheck disable=SC2206
args=("$@")
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
[ -n "$project" ] || project='testpilot'

project_allowed=0
for allowed in $ALLOWED_PROJECTS; do
  [ "$project" = "$allowed" ] && project_allowed=1
done

in_ci=0
case "${CI:-}" in 1|true|yes) in_ci=1 ;; esac
ci_hint=""
[ "$in_ci" -eq 1 ] && ci_hint=' В CI используйте отдельный проект: `-p testpilot-ci`.'

# Разрушительные операции на одноразовых проектах требуют явной идентичности стенда:
# имя проекта само по себе (или префикс) не доказывает, что это нужный стенд.
if { [ "$has_down" -eq 1 ] && [ "$has_volumes" -eq 1 ]; } || [ -n "$volume_sub" ]; then
  if [ "$project_allowed" -eq 1 ]; then
    if [ "${TP_DISPOSABLE_IDENTITY:-}" != "$project" ]; then
      echo "Отказано: разрушительная операция для $project требует TP_DISPOSABLE_IDENTITY=$project (явная идентичность одноразового стенда)." >&2
      exit 3
    fi
    if command -v docker >/dev/null 2>&1; then
      bad_volumes="$(docker volume ls --filter "label=com.docker.compose.project=$project" --format '{{.Name}}' 2>/dev/null | grep -v "^${project}_" || true)"
      if [ -n "$bad_volumes" ]; then
        echo "Отказано: у проекта $project есть тома с чужими именами: $bad_volumes" >&2
        exit 3
      fi
    fi
  fi
fi

if [ "$has_down" -eq 1 ] && [ "$has_volumes" -eq 1 ] && [ "$project_allowed" -eq 0 ]; then
  echo "Отказано: \`down -v\` удаляет тома (БД и evidence). На рабочем стенде недопустимо. Используйте \`-p testpilot-ci\` или \`-p testpilot-restore\`, либо \`docker compose down\` без \`-v\`.$ci_hint" >&2
  exit 3
fi

if [ -n "$volume_sub" ]; then
  case "$volume_sub" in
    prune)
      if [ "$project_allowed" -eq 0 ]; then
        echo "Отказано: \`volume prune\` может удалить тома проекта $project (БД и evidence). Используйте \`-p testpilot-ci\`/\`-p testpilot-restore\`.$ci_hint" >&2
        exit 3
      fi
      ;;
    rm)
      if [ "$project_allowed" -eq 0 ]; then
        for target in "${volume_targets[@]}"; do
          case "$target" in
            testpilot*)
              echo "Отказано: удаление томов проекта testpilot уничтожает БД и evidence. Используйте \`-p testpilot-ci\` или \`-p testpilot-restore\`.$ci_hint" >&2
              exit 3
              ;;
          esac
        done
      fi
      ;;
  esac
fi

docker compose "$@"
