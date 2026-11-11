from __future__ import annotations

import io
from pathlib import Path
from typing import Any
from zipfile import ZipFile, BadZipFile
from fastapi import APIRouter, Depends, HTTPException

from testpilot.common import user
from testpilot.db import session, UploadRow
from testpilot.scanner import ScannerEngine, Severity, ScanSummary

router = APIRouter(prefix="/api/v1/audit", tags=["Аудит и v8std"])
_engine = ScannerEngine()


def _summarize(issues, total_files: int) -> dict[str, Any]:
    sev_counts: dict[str, int] = {}
    cat_counts: dict[str, int] = {}
    for issue in issues:
        sev_counts[issue.severity.value] = sev_counts.get(issue.severity.value, 0) + 1
        cat_counts[issue.category.value] = cat_counts.get(issue.category.value, 0) + 1
    summary = ScanSummary(
        total_files=total_files,
        total_issues=len(issues),
        issues_by_severity=sev_counts,
        issues_by_category=cat_counts,
        passed=(sev_counts.get(Severity.CRITICAL.value, 0) == 0 and sev_counts.get(Severity.HIGH.value, 0) == 0),
        issues=issues,
    )
    return summary.to_dict()


@router.post("/scan")
async def scan_endpoint(data: dict[str, Any]) -> dict[str, Any]:
    code = str(data.get("code", "")).strip()
    path_str = str(data.get("path", "")).strip()
    min_sev_str = data.get("min_severity")
    min_sev = Severity(min_sev_str) if min_sev_str else None

    if code:
        issues = _engine.scan_code(code, "web_input.bsl")
        return _summarize(issues, 1)

    if path_str:
        p = Path(path_str).resolve()
        if p.is_file():
            issues = _engine.scan_file(p)
            return _summarize(issues, 1)
        elif p.is_dir():
            summary = _engine.scan_directory(p, min_severity=min_sev)
            return summary.to_dict()
        else:
            raise HTTPException(400, f"Указанный путь не найден: {p}")

    raise HTTPException(400, "Передайте 'code' или 'path' для анализа.")


@router.post("/scan-upload")
def scan_upload_endpoint(data: dict[str, Any], actor=Depends(user), s=Depends(session, scope="function")) -> dict[str, Any]:
    upload_id = str(data.get("upload_id", "")).strip()
    project_id = str(data.get("project_id", "")).strip()
    if not upload_id:
        raise HTTPException(400, "Укажите upload_id")

    row = s.get(UploadRow, upload_id)
    if not row or (project_id and row.project_id != project_id):
        raise HTTPException(404, "Загрузка не найдена")

    raw = row.content
    filename = row.filename.lower()

    if filename.endswith(".zip") or raw.startswith(b"PK\x03\x04"):
        try:
            all_issues = []
            scanned = 0
            with ZipFile(io.BytesIO(raw)) as z:
                for name in z.namelist():
                    if name.lower().endswith((".bsl", ".os", ".xml")):
                        text = z.read(name).decode("utf-8-sig", errors="replace")
                        all_issues.extend(_engine.scan_code(text, name))
                        scanned += 1
            return _summarize(all_issues, scanned)
        except BadZipFile:
            raise HTTPException(422, "Поврежденный ZIP-архив")

    text = raw.decode("utf-8-sig", errors="replace")
    issues = _engine.scan_code(text, row.filename)
    return _summarize(issues, 1)


@router.get("/rules")
def get_rules() -> list[dict[str, Any]]:
    return [
        {
            "rule_id": r.rule_id,
            "category": r.category.value,
            "severity": r.severity.value,
            "message": r.message,
            "recommendation": r.recommendation,
        }
        for r in _engine.rules
    ]
