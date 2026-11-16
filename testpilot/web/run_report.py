"""Отчёт о прогоне проверок сохранённого плана (suite) в md и csv."""
from __future__ import annotations

from typing import Any

from fastapi.responses import Response

from testpilot.web.report_render import attachment, csv_document, markdown_cell, validate_format

RUN_CSV_HEADER = 'Предложение;Движок;Проверка;Статус;Сообщение;Длительность, мс;Evidence'


def _approved_proposals(suite: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    selected = (suite.get('approval') or {}).get('selected_proposals') or []
    proposals = (suite.get('suggestion') or {}).get('proposals') or []
    return [(index, proposals[index]) for index in selected if 0 <= index < len(proposals)]


def _check_text(proposal: dict[str, Any]) -> str:
    """Что именно проверяется: шаги сценария для playwright, сущность/режим для odata."""
    if proposal.get('engine') == 'odata':
        case = (proposal.get('cases') or [{}])[0]
        entity = case.get('entity')
        check = case.get('check') or ('чтение коллекции' if entity else '$metadata')
        return f"{entity or '—'} · {check}"
    steps = list(proposal.get('steps') or [])
    if not steps:
        for case in proposal.get('cases') or []:
            for step in case.get('steps') or []:
                steps.append(f"{step.get('action', 'шаг')} {step.get('target', '')}".strip())
    return ' → '.join(steps) if steps else '—'


def _result_for(results: list[dict[str, Any]], index: int) -> dict[str, Any] | None:
    return next((result for result in results if result.get('proposal_index') == index), None)


def _result_cells(result: dict[str, Any] | None) -> list[str]:
    if result is None:
        return ['нет данных', 'Результат для этого предложения не сохранён', '', '']
    duration = result.get('duration_seconds')
    return [
        str(result.get('status') or ''),
        str(result.get('message') or ''),
        '' if duration is None else str(round(float(duration) * 1000)),
        str(result.get('evidence_id') or ''),
    ]


def _csv_body(suite: dict[str, Any], results: list[dict[str, Any]]) -> str:
    rows = []
    for index, proposal in _approved_proposals(suite):
        rows.append([
            proposal.get('title', ''),
            proposal.get('engine', ''),
            _check_text(proposal),
            *_result_cells(_result_for(results, index)),
        ])
    return csv_document(RUN_CSV_HEADER.split(';'), rows)


def _markdown_body(suite: dict[str, Any], project_name: str) -> str:
    counts = suite.get('counts') or {}
    provider = suite.get('provider') or '—'
    model = suite.get('model')
    results = suite.get('results') or []
    lines = [
        '# Отчёт о прогоне проверок',
        '',
        f'Проект: {project_name}',
        f"Пакет: {suite.get('package_id', '')}",
        f"Провайдер: {provider}" + (f' · модель: {model}' if model else ''),
        f"Дата: {suite.get('created_at', '')}",
        f"Статус: {suite.get('status', '')}",
    ]
    if suite.get('finished_at'):
        lines.append(f"Завершён: {suite['finished_at']}")
    if suite.get('executor'):
        lines.append(f"Исполнитель: {suite['executor']}")
    lines += [
        '',
        '## Сводка',
        f"- Пройдено: {counts.get('passed', 0)}",
        f"- Расхождений: {counts.get('failed', 0)}",
        f"- Ошибок: {counts.get('error', 0)}",
        f"- Пропущено: {counts.get('skipped', 0)}",
        '',
    ]
    if not results:
        lines += ['## Результаты', '', 'Результатов нет: прогон ещё не выполнялся.', '']
    lines += ['## Утверждённые проверки', '']
    approved = _approved_proposals(suite)
    if not approved:
        lines += ['Утверждённых предложений нет.', '']
    for number, (index, proposal) in enumerate(approved, 1):
        status, message, duration, evidence = _result_cells(_result_for(results, index))
        lines += [
            f"### {number}. {proposal.get('title', '')}",
            '',
            f"- Движок: {proposal.get('engine', '')}",
            f"- Проверка: {_check_text(proposal)}",
            f"- Результат: {markdown_cell(status)} · {markdown_cell(message)} · "
            f"{duration or '—'} мс · evidence {evidence or '—'}",
            '',
        ]
    return '\n'.join(lines)


def render_run_report(suite: dict[str, Any], project_name: str, format: str) -> Response:
    """Отдаёт отчёт по сохранённому прогону плана; ничего не пересчитывает."""
    validate_format(format)
    if format == 'csv':
        body = _csv_body(suite, suite.get('results') or [])
    else:
        body = _markdown_body(suite, project_name)
    return attachment(body, format, 'run', str(suite.get('suite_id', '')))
