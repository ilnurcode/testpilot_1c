"""Транзакционное сохранение нормализованной версии, общее для обоих API импорта."""
import hashlib
from fastapi import HTTPException
from sqlalchemy import select
from testpilot.contracts import checksum
from testpilot.db import ConfigurationRow,VersionRow,ObjectRow,EdgeRow,audit


def store_version(s,model,raw,actor):
    payload=model.content();digest=checksum(payload)
    old=s.scalar(select(VersionRow).where(VersionRow.configuration_id==model.configuration_id,VersionRow.version==model.version))
    if old:
        if old.checksum!=digest:raise HTTPException(409,'Этот номер версии уже сохранён с другим содержимым')
        return {'id':old.id,'checksum':digest,'idempotent':True}
    config=s.get(ConfigurationRow,model.configuration_id)
    if config and config.synthetic!=model.synthetic:raise HTTPException(409,'Нельзя смешивать учебные и реальные версии конфигурации')
    if not config:
        s.add(ConfigurationRow(id=model.configuration_id,synthetic=model.synthetic));s.flush()
    row=VersionRow(configuration_id=model.configuration_id,version=model.version,source=model.source,checksum=digest,raw_checksum=hashlib.sha256(raw).hexdigest(),payload=payload)
    s.add(row);s.flush()
    for o in payload['objects']:s.add(ObjectRow(version_id=row.id,**o))
    s.flush()
    for e in payload['edges']:s.add(EdgeRow(version_id=row.id,**e))
    audit(s,'impact',actor,'import',row.id,{'checksum':digest,'raw_checksum':row.raw_checksum})
    return {'id':row.id,'checksum':digest,'idempotent':False}
