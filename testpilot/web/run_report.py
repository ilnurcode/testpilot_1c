"""Отчёт о прогоне проверок сохранённого плана (suite) в md и csv."""
from __future__ import annotations

from typing import Any

from fastapi.responses import Response

from testpilot.web.report_render import attachment, csv_document, markdown_cell, validate_format

RUN_CSV_HEADER = 'Предложение;Case;Движок;Проверка;Статус;Сообщение;Длительность, мс;Evidence'

ODATA_404_HINT = (
    'База не публикует эту сущность в OData (публикация с 0 EntitySet или другой состав). '
    'Проверку сущности на этой базе выполнить нельзя; доступность сервиса проверяется случаем `$metadata`.'
)


def _odata_404_hint(result: dict[str, Any], proposal: dict[str, Any]) -> str | None:
    """Пояснение к отказу 404 по сущности: это свойство публикации, а не обязательно дефект изменения."""
    engine = result.get('engine') or proposal.get('engine') or ''
    message = str(result.get('message') or '')
    if engine == 'odata' and 'не найдена (HTTP 404)' in message:
        return ODATA_404_HINT
    return None


def _approved_proposals(suite: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    selected = (suite.get('approval') or {}).get('selected_proposals') or []
    proposals = (suite.get('suggestion') or {}).get('proposals') or []
    return [(index, proposals[index]) for index in selected if 0 <= index < len(proposals)]


def _proposal_at(suite: dict[str, Any], index: Any) -> dict[str, Any]:
    proposals = (suite.get('suggestion') or {}).get('proposals') or []
    if isinstance(index, int) and 0 <= index < len(proposals):
        return proposals[index]
    return {}


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


def _case_number(result: dict[str, Any]) -> Any:
    """Номер кейса для отчёта: 1-based, как «набор N» в интерфейсе прогона."""
    index = result.get('case_index')
    return index + 1 if isinstance(index, int) else '—'


def _result_cells(result: dict[str, Any]) -> list[str]:
    duration = result.get('duration_seconds')
    return [
        str(result.get('status') or '—'),
        str(result.get('message') or ''),
        '' if duration is None else str(round(float(duration) * 1000)),
        str(result.get('evidence_id') or ''),
    ]


def _missing_cells() -> list[str]:
    return ['—', 'результата нет', '', '']


def _csv_body(suite: dict[str, Any]) -> str:
    proposals = (suite.get('suggestion') or {}).get('proposals') or []
    results = suite.get('results') or []
    rows = []
    covered: set[Any] = set()
    for result in results:
        index = result.get('proposal_index')
        proposal = _proposal_at(suite, index)
        covered.add(index)
        rows.append([
            proposal.get('title', ''),
            _case_number(result),
            proposal.get('engine', ''),
            _check_text(proposal),
            *_result_cells(result),
        ])
    for index, proposal in _approved_proposals(suite):
        if index in covered:
            continue
        rows.append([proposal.get('title', ''), '—', proposal.get('engine', ''), _check_text(proposal), *_missing_cells()])
    return csv_document(RUN_CSV_HEADER.split(';'), rows)


def _proposal_line(number: int, proposal: dict[str, Any], results: list[dict[str, Any]]) -> list[str]:
    lines = [
        f"### {number}. {proposal.get('title', '')}",
        '',
        f"- Движок: {proposal.get('engine', '')}",
        f"- Проверка: {_check_text(proposal)}",
        '- Результаты:',
    ]
    if not results:
        lines.append('- результата нет (проверка не выполнялась)')
    for result in results:
        status, message, duration, evidence = _result_cells(result)
        lines.append(
            f"- case {_case_number(result)}: {markdown_cell(status)} · {markdown_cell(message)} · "
            f"{duration or '—'} мс · evidence {evidence or '—'}"
        )
        hint = _odata_404_hint(result, proposal)
        if hint:
            lines.append(f'  - пояснение: {hint}')
    lines.append('')
    return lines


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
        f"- Строгий режим: {'да (только проверки без кликов)' if suite.get('strict_read_only') else 'нет'}",
        f"- Изменяющие шаги: {'разрешены' if suite.get('allow_mutations') else 'не разрешены'} (эвристика по тексту кнопки)",
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
        proposal_results = [result for result in results if result.get('proposal_index') == index]
        lines += _proposal_line(number, proposal, proposal_results)
    return '\n'.join(lines)


def render_run_report(suite: dict[str, Any], project_name: str, format: str) -> Response:
    """Отдаёт отчёт по сохранённому прогону плана; одна строка csv — один результат кейса."""
    validate_format(format)
    if format == 'csv':
        body = _csv_body(suite)
    else:
        body = _markdown_body(suite, project_name)
    return attachment(body, format, 'run', str(suite.get('suite_id', '')))
