import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from testpilot.common import user
from testpilot.db import ProjectRow, UploadRow, session

router = APIRouter(prefix='/api/v1/audit/uploads', tags=['Аудит и v8std'])


@router.get('/{upload_id}/report')
def get_upload_audit_report(
    upload_id: str,
    format: str = 'md',
    actor=Depends(user),
    s=Depends(session, scope='function'),
) -> Response:
    if format not in {'md', 'csv'}:
        raise HTTPException(400, 'Неподдерживаемый формат отчёта: используйте md или csv')
    row = s.get(UploadRow, upload_id)
    if row is None or row.audit_result is None:
        raise HTTPException(404, 'Сохранённый аудит не найден')
    project = s.get(ProjectRow, row.project_id)
    project_name = project.name if project else row.project_id
    result = row.audit_result
    issues = result.get('issues', [])
    if format == 'csv':
        output = io.StringIO(newline='')
        writer = csv.writer(output, delimiter=';', lineterminator='\r\n')
        writer.writerow(['Правило', 'Уровень', 'Файл:строка', 'Сообщение', 'Фрагмент кода'])
        for issue in issues:
            writer.writerow([
                issue.get('rule_id', ''),
                issue.get('severity', ''),
                f"{issue.get('file_path', '')}:{issue.get('line', '')}",
                issue.get('message', ''),
                issue.get('code_fragment', ''),
            ])
        body = output.getvalue()
        media_type = 'text/csv; charset=utf-8'
    else:
        severities = result.get('issues_by_severity', {})
        levels = ', '.join(f'{level}: {count}' for level, count in severities.items()) or 'нет'
        passed = 'да' if result.get('passed') else 'нет'
        lines = [
            f'# Отчёт аудита — {project_name} / {row.filename}',
            '',
            f'Дата: {row.created_at}',
            '',
            '## Сводка',
            f"- Всего замечаний: {result.get('total_issues', 0)}",
            f'- По уровням: {levels}',
            f'- Пройдено: {passed}',
            '',
            '## Замечания',
            '',
            '| Правило | Уровень | Файл:строка | Сообщение | Фрагмент кода |',
            '| --- | --- | --- | --- | --- |',
        ]

        def cell(value: Any) -> str:
            text = str(value or '')
            return text.replace('\r\n', '<br>').replace('\n', '<br>').replace('\r', '<br>').replace('|', r'\|')

        for issue in issues:
            lines.append(
                f"| {cell(issue.get('rule_id'))} | {cell(issue.get('severity'))} | "
                f"{cell(issue.get('file_path'))}:{cell(issue.get('line'))} | "
                f"{cell(issue.get('message'))} | {cell(issue.get('code_fragment'))} |"
            )
        body = '\n'.join(lines) + '\n'
        media_type = 'text/markdown; charset=utf-8'
    return Response(
        content=body,
        media_type=media_type,
        headers={'Content-Disposition': f'attachment; filename="audit-{row.id}.{format}"'},
    )
