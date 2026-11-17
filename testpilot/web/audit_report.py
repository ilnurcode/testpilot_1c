"""Рендер отчёта аудита: используется и для загрузок, и для журнала сканов."""
from __future__ import annotations

from typing import Any

from fastapi.responses import Response

from testpilot.web.report_render import attachment, csv_document, markdown_cell, validate_format

AUDIT_CSV_HEADER = 'Правило;Уровень;Файл:строка;Сообщение;Фрагмент кода'
AUDIT_MARKDOWN_HEADER = '| Правило | Уровень | Файл:строка | Сообщение | Фрагмент кода |'


def _issue_row(issue: dict[str, Any]) -> list[Any]:
    return [
        issue.get('rule_id', ''),
        issue.get('severity', ''),
        f"{issue.get('file_path', '')}:{issue.get('line', '')}",
        issue.get('message', ''),
        issue.get('code_fragment', ''),
    ]


def truncation_note(result: dict[str, Any]) -> str | None:
    """Строка о неполной детализации: печатается, только если срез сработал."""
    total = int(result.get('total_issues') or 0)
    kept = len(result.get('issues') or [])
    if not (result.get('issues_truncated') or kept < total):
        return None
    return f'показаны первые {kept} из {total} замечаний (детализация ограничена)'


def _markdown_body(heading: str, created_at: str, result: dict[str, Any], issues: list[dict[str, Any]]) -> str:
    levels = ', '.join(f"{level}: {count}" for level, count in result.get('issues_by_severity', {}).items()) or 'нет'
    lines = [
        f'# Отчёт аудита — {heading}',
        '',
        f'Дата: {created_at}',
        '',
        '## Сводка',
        f"- Всего замечаний: {result.get('total_issues', 0)}",
        f'- По уровням: {levels}',
        f"- Пройдено: {'да' if result.get('passed') else 'нет'}",
        '',
        '## Замечания',
        '',
    ]
    note = truncation_note(result)
    if note:
        lines += [f'> {note}', '']
    lines += [
        AUDIT_MARKDOWN_HEADER,
        '| --- | --- | --- | --- | --- |',
    ]
    for issue in issues:
        lines.append(
            f"| {markdown_cell(issue.get('rule_id'))} | {markdown_cell(issue.get('severity'))} | "
            f"{markdown_cell(issue.get('file_path'))}:{markdown_cell(issue.get('line'))} | "
            f"{markdown_cell(issue.get('message'))} | {markdown_cell(issue.get('code_fragment'))} |"
        )
    return '\n'.join(lines) + '\n'


def render_audit_report(record_id: str, heading: str, created_at: str, result: dict[str, Any], format: str) -> Response:
    """Отдаёт отчёт по сохранённому результату аудита в md или csv.

    Полный исходник не сохраняется: в замечаниях есть только фрагменты строк (code_fragment).
    """
    validate_format(format)
    issues = result.get('issues', [])
    if format == 'csv':
        body = csv_document(AUDIT_CSV_HEADER.split(';'), [_issue_row(issue) for issue in issues])
    else:
        body = _markdown_body(heading, created_at, result, issues)
    response = attachment(body, format, 'audit', record_id)
    # CSV остаётся строго табличным (шапка + сохранённые строки), поэтому срез виден в заголовке ответа.
    response.headers['X-Audit-Issues-Kept'] = str(len(issues))
    response.headers['X-Audit-Issues-Total'] = str(int(result.get('total_issues') or 0))
    response.headers['X-Audit-Issues-Truncated'] = 'true' if truncation_note(result) else 'false'
    return response
