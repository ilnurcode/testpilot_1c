# Ранбук: пересоздать демо-данные с нуля

Данные создаются заново: этим путём **не вернуть** прежние 33 проекта, 45 suite, 42 анализа и 107 evidence. Пароль — `TP_EXPERT_PASSWORD` из `.env` (или дефолт compose), на экран не печатать.

## Предусловие

```
docker compose up -d --build --wait                    # все сервисы healthy
curl -s http://127.0.0.1:8000/health                   # {"status":"ok",...}
docker compose exec -T intelligence alembic current    # 0008 (head)
```

## Шаги (impact 8001, web 8000, intelligence 8002)

1. Проект:

```
curl -s -u "expert:$PASS" -H 'Content-Type: application/json' \
  -d '{"name":"Демо 2026-11-17","description":"пересоздание демо"}' \
  http://127.0.0.1:8001/api/v1/projects
```

Ожидание: 200, в ответе `id` проекта.

2. Демо-ZIP (тело — сырые байты файла):

```
curl -s -u "expert:$PASS" -X POST --data-binary @samples/demo_vulnerable_extension.zip \
  -H 'Content-Type: application/octet-stream' \
  "http://127.0.0.1:8001/api/v1/projects/<pid>/uploads?filename=demo_vulnerable_extension.zip&kind=extension&component=Demo"
```

Ожидание: 200, `status=ready`, в ответе `id` загрузки (`<uid>`).

3. Аудит загрузки (web): `POST /api/v1/audit/scan-upload` с `{"project_id":"<pid>","upload_id":"<uid>"}`.
Ожидание: `total_issues=12`, `{"CRITICAL":1,"HIGH":7,"MEDIUM":1,"LOW":3}`, `passed=false`.

4. Анализ влияния (impact): `POST /api/v1/projects/<pid>/uploads/<uid>/analyze` с `{"baseline_id":"saved","max_depth":10}`.
Ожидание: `affected_objects` = 4, в ответе `analysis_id` и `package`.

5. Доставка пакета в intelligence (impact): `POST /api/v1/analyses/<analysis_id>/deliver` (тело не нужно).
Ожидание: 200 и `package_id`. Именно этот шаг создаёт пакет в сервисе intelligence; без него
`POST /api/v1/ai/suites` отвечает **404** «Запись не найдена».

6. План проверок (intelligence, работает без ключа LLM):
`POST /api/v1/ai/suites` с `{"package_id":"<pkg>","project_id":"<pid>","provider":"local"}` → `<sid>`;
`POST /api/v1/ai/suites/<sid>/approve` с `{"reason":"пересоздание демо","selected_proposals":[0]}`;
`POST /api/v1/ai/suites/<sid>/run-1c`. Ожидание: `counts.passed=1`.
Без `web_url`/`odata_url` и пользователя в настройках проекта (или `TP_1C_*` в окружении) — **422**
«Параметры подключения к 1С не настроены».

7. Артефакты (только чтение): `GET /api/v1/ai/suites/<sid>/report?format=md` (8002) и
`GET /api/v1/audit/uploads/<uid>/report?format=md` (8000) → 200 `text/markdown`.
8. Бэкап: `bash scripts/backup.sh` → `.runtime/backups/testpilot-<дата>.sql`, размер > 0.
