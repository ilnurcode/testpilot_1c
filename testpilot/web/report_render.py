"""Общие примитивы экспорта отчётов: csv-документ, markdown-ячейка, attachment-ответ."""
from __future__ import annotations

import csv
import io
from typing import Any, Iterable, Sequence

from fastapi import HTTPException
from fastapi.responses import Response

SUPPORTED_FORMATS = ('md', 'csv')
_FORMAT_HINT = 'Неподдерживаемый формат отчёта: используйте md или csv'


def validate_format(format: str) -> str:
    if format not in SUPPORTED_FORMATS:
        raise HTTPException(400, _FORMAT_HINT)
    return format


def markdown_cell(value: Any) -> str:
    """Значение для ячейки markdown-таблицы: без переводов строк и с экранированной чертой."""
    text = str(value or '')
    return text.replace('\r\n', '<br>').replace('\n', '<br>').replace('\r', '<br>').replace('|', r'\|')


def csv_document(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    """CSV с разделителем «;» и CRLF; шапка пишется как есть, строки — через csv.writer."""
    output = io.StringIO(newline='')
    output.write(';'.join(header) + '\r\n')
    csv.writer(output, delimiter=';', lineterminator='\r\n').writerows(rows)
    return output.getvalue()


def attachment(body: str, format: str, prefix: str, record_id: str) -> Response:
    """Ответ-вложение audit-<id>.md|run-<id>.csv с соответствующим Content-Type."""
    validate_format(format)
    media_type = 'text/csv; charset=utf-8' if format == 'csv' else 'text/markdown; charset=utf-8'
    return Response(
        content=body,
        media_type=media_type,
        headers={'Content-Disposition': f'attachment; filename="{prefix}-{record_id}.{format}"'},
    )
