from __future__ import annotations

from pathlib import Path
from typing import Any
from fastapi import APIRouter, HTTPException

from testpilot.scanner import ScannerEngine, Severity, ScanSummary

router = APIRouter(prefix="/api/v1/audit", tags=["Аудит и v8std"])
_engine = ScannerEngine()


@router.post("/scan")
async def scan_endpoint(data: dict[str, Any]) -> dict[str, Any]:
    code = str(data.get("code", "")).strip()
    path_str = str(data.get("path", "")).strip()
    min_sev_str = data.get("min_severity")
    min_sev = Severity(min_sev_str) if min_sev_str else None

    if code:
        issues = _engine.scan_code(code, "web_input.bsl")
        sev_counts: dict[str, int] = {}
        cat_counts: dict[str, int] = {}
        for issue in issues:
            sev_counts[issue.severity.value] = sev_counts.get(issue.severity.value, 0) + 1
            cat_counts[issue.category.value] = cat_counts.get(issue.category.value, 0) + 1
        summary = ScanSummary(
            total_files=1,
            total_issues=len(issues),
            issues_by_severity=sev_counts,
            issues_by_category=cat_counts,
            passed=(sev_counts.get(Severity.CRITICAL.value, 0) == 0 and sev_counts.get(Severity.HIGH.value, 0) == 0),
            issues=issues,
        )
        return summary.to_dict()

    if path_str:
        p = Path(path_str).resolve()
        if p.is_file():
            issues = _engine.scan_file(p)
            sev_counts = {}
            cat_counts = {}
            for issue in issues:
                sev_counts[issue.severity.value] = sev_counts.get(issue.severity.value, 0) + 1
                cat_counts[issue.category.value] = cat_counts.get(issue.category.value, 0) + 1
            summary = ScanSummary(
                total_files=1,
                total_issues=len(issues),
                issues_by_severity=sev_counts,
                issues_by_category=cat_counts,
                passed=(sev_counts.get(Severity.CRITICAL.value, 0) == 0 and sev_counts.get(Severity.HIGH.value, 0) == 0),
                issues=issues,
            )
            return summary.to_dict()
        elif p.is_dir():
            summary = _engine.scan_directory(p, min_severity=min_sev)
            return summary.to_dict()
        else:
            raise HTTPException(400, f"Указанный путь не найден: {p}")

    raise HTTPException(400, "Передайте 'code' или 'path' для анализа.")


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
