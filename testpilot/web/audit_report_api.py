from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from testpilot.common import user
from testpilot.db import ProjectRow, UploadRow, session
from testpilot.web.audit_report import render_audit_report

router = APIRouter(prefix='/api/v1/audit/uploads', tags=['Аудит и v8std'])


@router.get('/{upload_id}/report')
def get_upload_audit_report(
    upload_id: str,
    format: str = 'md',
    actor=Depends(user),
    s=Depends(session, scope='function'),
) -> Response:
    row = s.get(UploadRow, upload_id)
    if row is None or row.audit_result is None:
        raise HTTPException(404, 'Сохранённый аудит не найден')
    project = s.get(ProjectRow, row.project_id)
    project_name = project.name if project else row.project_id
    return render_audit_report(
        row.id,
        f'{project_name} / {row.filename}',
        row.created_at,
        row.audit_result,
        format,
    )
