"""Единый рендер отчёта аудита: используется и для загрузок, и для журнала сканов."""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import HTTPException
from fastapi.responses import Response

SUPPORTED_FORMATS = ("md", "csv")
CSV_HEADER = "Правило;Уровень;Файл:строка;Сообщение;Фрагмент кода"
MARKDOWN_HEADER = "| Правило | Уровень | Файл:строка | Сообщение | Фрагмент кода |"


def _markdown_cell(value: Any) -> str:
    text = str(value or "")
    return text.replace("\r\n", "<br>").replace("\n", "<br>").replace("\r", "<br>").replace("|", r"\|")


def _csv_body(issues: list[dict[str, Any]]) -> str:
    output = io.StringIO(newline="")
    output.write(CSV_HEADER + "\r\n")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    for issue in issues:
        writer.writerow([
            issue.get("rule_id", ""),
            issue.get("severity", ""),
            f"{issue.get('file_path', '')}:{issue.get('line', '')}",
            issue.get("message", ""),
            issue.get("code_fragment", ""),
        ])
    return output.getvalue()


def _markdown_body(heading: str, created_at: str, result: dict[str, Any], issues: list[dict[str, Any]]) -> str:
    levels = ", ".join(f"{level}: {count}" for level, count in result.get("issues_by_severity", {}).items()) or "нет"
    lines = [
        f"# Отчёт аудита — {heading}",
        "",
        f"Дата: {created_at}",
        "",
        "## Сводка",
        f"- Всего замечаний: {result.get('total_issues', 0)}",
        f"- По уровням: {levels}",
        f"- Пройдено: {'да' if result.get('passed') else 'нет'}",
        "",
        "## Замечания",
        "",
        MARKDOWN_HEADER,
        "| --- | --- | --- | --- | --- |",
    ]
    for issue in issues:
        lines.append(
            f"| {_markdown_cell(issue.get('rule_id'))} | {_markdown_cell(issue.get('severity'))} | "
            f"{_markdown_cell(issue.get('file_path'))}:{_markdown_cell(issue.get('line'))} | "
            f"{_markdown_cell(issue.get('message'))} | {_markdown_cell(issue.get('code_fragment'))} |"
        )
    return "\n".join(lines) + "\n"


def render_audit_report(record_id: str, heading: str, created_at: str, result: dict[str, Any], format: str) -> Response:
    """Отдаёт отчёт по сохранённому результату аудита в md или csv.

    Полный исходник не сохраняется: в замечаниях есть только фрагменты строк (code_fragment).
    """
    if format not in SUPPORTED_FORMATS:
        raise HTTPException(400, "Неподдерживаемый формат отчёта: используйте md или csv")
    issues = result.get("issues", [])
    if format == "csv":
        body, media_type = _csv_body(issues), "text/csv; charset=utf-8"
    else:
        body, media_type = _markdown_body(heading, created_at, result, issues), "text/markdown; charset=utf-8"
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="audit-{record_id}.{format}"'},
    )
