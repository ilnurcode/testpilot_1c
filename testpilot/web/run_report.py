"""Отчёт о прогоне проверок сохранённого плана (suite) в md и csv."""
from __future__ import annotations

import html
import json
import re
from typing import Any

from fastapi.responses import Response

from testpilot.web.report_render import attachment, csv_document, markdown_cell, validate_format

RUN_CSV_HEADER = 'Предложение;Case;Движок;Проверка;Статус;Сообщение;Длительность, мс;Evidence;Статус прогона;Бизнес-результат;Assertions;Ожидалось;Получено;Источник evidence;Причина отсутствия снимка;Источник ошибки;Сообщение прогона'

ODATA_404_HINT = (
    'Возможная причина: EntitySet не опубликован, неверный base URL/путь или имя сущности. '
    'Проверьте `$metadata` (доступность сервиса) и имя сущности; статус проверки остаётся failed.'
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


def _check_text(proposal: dict[str, Any], case_index: Any = None) -> str:
    cases = proposal.get('cases') or []
    case = cases[case_index] if isinstance(case_index, int) and 0 <= case_index < len(cases) else {}
    if proposal.get('engine') == 'odata':
        entity = case.get('entity')
        assertions = case.get('assertions') or []
        if assertions:
            checks = ', '.join(f"{item.get('scope', '—')}: {item.get('check', '—')}" for item in assertions)
            return f"{entity or '—'} · {checks}"
        check = case.get('check') or ('чтение коллекции' if entity else '$metadata')
        return f"{entity or '—'} · {check}"
    steps = [
        f"{step.get('action', 'шаг')} {step.get('target', '')}".strip()
        for step in case.get('steps') or []
    ] or list(proposal.get('steps') or [])
    return ' → '.join(steps) if steps else '—'


def _case_number(result: dict[str, Any]) -> Any:
    """Номер кейса для отчёта: 1-based, как «набор N» в интерфейсе прогона."""
    index = result.get('case_index')
    return index + 1 if isinstance(index, int) else '—'


def _text(value: Any) -> str:
    """Недоверенные значения не становятся HTML, ссылками или разметкой Markdown."""
    text = html.escape(str(value if value is not None else ''), quote=True)
    return markdown_cell(re.sub(r'([\\`*_{}\[\]()!#])', r'\\\1', text))


def _value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if value is not None else 'null'


def _business(value: Any) -> str:
    return 'подтверждён' if value is True else 'не подтверждён' if value is False else 'не проверен / не доказан'


def _report_cases(suite: dict[str, Any]) -> list[tuple[dict, dict]]:
    results = suite.get('results') or []
    rows = [(_proposal_at(suite, result.get('proposal_index')), result) for result in results]
    present = {(result.get('proposal_index'), result.get('case_index')) for result in results}
    for index, proposal in _approved_proposals(suite):
        for case_index in range(max(len(proposal.get('cases') or []), 1)):
            if (index, case_index) not in present:
                rows.append((proposal, {'proposal_index': index, 'case_index': case_index,
                                        'status': 'not_performed', 'message': 'не выполнено: результата нет'}))
    return rows


def _result_cells(result: dict[str, Any]) -> list[str]:
    duration = result.get('duration_seconds')
    return [
        str(result.get('status') or '—'),
        str(result.get('message') or ''),
        '' if duration is None else str(round(float(duration) * 1000)),
        str(result.get('evidence_id') or ''),
    ]


def _csv_body(suite: dict[str, Any]) -> str:
    rows = []
    for proposal, result in _report_cases(suite):
        row = [
            result.get('title') or proposal.get('title', ''),
            _case_number(result), result.get('engine') or proposal.get('engine', ''),
            _check_text(proposal, result.get('case_index')), *_result_cells(result),
            suite.get('status', ''), _business(result.get('business_success')),
            _value(result.get('assertions') or []),
            _value(result.get('expected')), _value(result.get('actual')),
            result.get('evidence_provenance', ''), result.get('screenshot_unavailable_reason', ''),
            _value(result.get('error_provenance')), suite.get('message', ''),
        ]
        # Открытие CSV в таблице не должно исполнять формулы из текста модели/1С.
        rows.append(["'" + cell if isinstance(cell, str) and cell.lstrip().startswith(('=', '+', '-', '@')) else cell
                     for cell in row])
    return csv_document(RUN_CSV_HEADER.split(';'), rows)


def _proposal_line(number: int, proposal: dict[str, Any], results: list[dict[str, Any]]) -> list[str]:
    lines = [
        f"### {number}. {_text((results[0].get('title') if results else None) or proposal.get('title', ''))}", '',
        f"- Движок: {_text(proposal.get('engine', ''))}",
    ]
    for result in results:
        status, message, duration, evidence = _result_cells(result)
        lines += [
            f"- case {_case_number(result)}: {_text(status)} · {_text(message)} · "
            f"{duration or '—'} мс · evidence {_text(evidence) or '—'}",
            f"  - Проверка: {_text(_check_text(proposal, result.get('case_index')))}",
            f"  - Бизнес-результат: {_business(result.get('business_success'))}",
        ]
        if 'expected' in result or 'actual' in result:
            lines += [f"  - Ожидалось: {_text(_value(result.get('expected')))}",
                      f"  - Получено: {_text(_value(result.get('actual')))}"]
        for assertion in result.get('assertions') or []:
            lines += [
                f"  - Assertion {_text(assertion.get('assertion_id'))}: "
                f"scope={_text(assertion.get('scope'))}, status={_text(assertion.get('status'))}",
                f"    - Ожидалось: {_text(_value(assertion.get('expected')))}",
                f"    - Получено: {_text(_value(assertion.get('actual')))}",
                f"    - Сообщение: {_text(assertion.get('message'))}",
            ]
        if not result.get('assertions'):
            lines.append('  - Typed assertions отсутствуют: бизнес-результат по ним не доказан.')
        provenance = result.get('evidence_provenance')
        if provenance:
            post = result.get('post_click_screenshot_bytes') or 0
            pre = result.get('pre_submit_screenshot_bytes') or 0
            if provenance == 'none':
                reason = _text(result.get('screenshot_unavailable_reason') or 'неизвестная причина')
                lines.append(f"- Evidence: нет снимка: {reason} (пост-клик {post} Б, до отправки {pre} Б)")
            else:
                lines.append(f"- Evidence: {_text(provenance)}, пост-клик {post} Б, до отправки {pre} Б")
        if result.get('error_provenance'):
            lines.append(f"  - Источник ошибки: {_text(_value(result['error_provenance']))}")
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
        f'Проект: {_text(project_name)}',
        f"Пакет: {_text(suite.get('package_id', ''))}",
        f"Провайдер: {_text(provider)}" + (f' · модель: {_text(model)}' if model else ''),
        f"Дата: {_text(suite.get('created_at', ''))}",
        f"Статус: {_text(suite.get('status', ''))}",
        f"- Бизнес-результат: {_business(suite.get('business_success'))}",
        '- Connectivity/metadata passed не подтверждают бизнес-требования.',
        '- Отмена/ошибка/прерывание не отменяют уже выполненные действия в 1С.',
        f"- Строгий режим: {'да (только проверки без кликов)' if suite.get('strict_read_only') else 'нет'}",
        f"- Изменяющие шаги: {'разрешены' if suite.get('allow_mutations') else 'не разрешены'} (эвристика по тексту кнопки)",
    ]
    if suite.get('message'):
        lines.append(f"Сообщение прогона: {_text(suite['message'])}")
    if suite.get('finished_at'):
        lines.append(f"Завершён: {_text(suite['finished_at'])}")
    if suite.get('executor'):
        lines.append(f"Исполнитель: {_text(suite['executor'])}")
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
        text = 'Результатов нет: прогон ещё не выполнялся.' if suite.get('status') in ('draft', 'approved', None) else 'Сохранённых результатов кейсов нет; успешность не доказана.'
        lines += ['## Результаты', '', text, '']
    lines += ['## Проверки по кейсам', '']
    rows = _report_cases(suite)
    if not rows:
        lines += ['Сохранённых кейсов нет.', '']
    for number, (proposal, result) in enumerate(rows, 1):
        lines += _proposal_line(number, proposal, [result])
    return '\n'.join(lines)


def render_run_report(suite: dict[str, Any], project_name: str, format: str) -> Response:
    """Отдаёт отчёт по сохранённому прогону плана; одна строка csv — один результат кейса."""
    validate_format(format)
    if format == 'csv':
        body = _csv_body(suite)
    else:
        body = _markdown_body(suite, project_name)
    return attachment(body, format, 'run', str(suite.get('suite_id', '')))
